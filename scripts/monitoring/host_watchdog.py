#!/usr/bin/env python3
"""Host watchdog for the PU Workspace production host.

Runs every 5 minutes from a systemd timer (see docs/operations/MONITORING_RU.md).
It only reads: disk/memory stats, `docker ps`, read-only SQL through
`docker exec ... psql`, and the loopback backend health endpoint. Problems are
reported to Telegram once per problem per REPEAT_AFTER seconds, and a
"восстановлено" message is sent when a problem clears.

Short stops during deployments (containers and the DB are down for ~1 minute)
must not page anyone: a non-disk/non-memory problem is reported only if it
persists for MIN_PROBLEM_AGE seconds (default 3 minutes, see first_seen in the
state file). Disk and memory problems are reported immediately.

Standard library only: it runs on the host's python3, not inside a container.
Secrets (the bot token) are read from the prod env file at send time and are
never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PREFIX = os.getenv("WATCHDOG_CONTAINER_PREFIX", "puw-primary-next-")
BACKEND = os.getenv("WATCHDOG_BACKEND_CONTAINER", PREFIX + "primary-backend")
DB = os.getenv("WATCHDOG_DB_CONTAINER", PREFIX + "primary-db")
# Containers that must exist; `worker-*` means "at least one container with this prefix".
EXPECTED = os.getenv(
    "WATCHDOG_EXPECTED_CONTAINERS",
    "primary-backend,primary-db,scheduler-1,worker-*,telegram-relay",
)
HEALTH_URL = os.getenv("WATCHDOG_HEALTH_URL", "http://127.0.0.1:3020/health")
ENV_FILE = Path(os.getenv("WATCHDOG_ENV_FILE", "/opt/pu-workspace-primary/shared/.env.primary"))
STATE_DIR = Path(os.getenv("WATCHDOG_STATE_DIR", "/var/lib/pu-watchdog"))
DISABLED = {item.strip() for item in os.getenv("WATCHDOG_DISABLE", "").split(",") if item.strip()}
HOST = os.getenv("WATCHDOG_HOST_LABEL", f"prod, {socket.gethostname()}")

DISK_PATHS = ("/", "/mnt/data")
DISK_THRESHOLD = 80.0          # percent used
MEM_THRESHOLD = 10.0           # percent available
HEARTBEAT_MAX_AGE = 5 * 60     # seconds
GMAIL_MAX_AGE = 30 * 60        # seconds
REPEAT_AFTER = 6 * 60 * 60     # seconds between repeated alerts for one problem
MIN_PROBLEM_AGE = int(os.getenv("PU_WATCHDOG_MIN_PROBLEM_AGE", "180"))  # seconds a problem must persist


@dataclass(frozen=True)
class Problem:
    key: str
    message: str


# --------------------------------------------------------------------------- pure logic


def disk_problem(path: str, used_percent: float, threshold: float = DISK_THRESHOLD) -> Problem | None:
    if used_percent >= threshold:
        return Problem(f"disk:{path}", f"Диск {path} заполнен на {used_percent:.0f}% (порог {threshold:.0f}%)")
    return None


def memory_problem(total_kb: int, available_kb: int, threshold: float = MEM_THRESHOLD) -> Problem | None:
    if total_kb <= 0:
        return None
    percent = available_kb * 100.0 / total_kb
    if percent < threshold:
        return Problem("memory", f"Мало памяти: доступно {available_kb // 1024} МБ ({percent:.1f}%, порог {threshold:.0f}%)")
    return None


def container_problems(rows: list[tuple[str, str, str]], expected: str = EXPECTED, prefix: str = PREFIX) -> list[Problem]:
    """rows are (name, state, status) from `docker ps -a`."""
    problems: list[Problem] = []
    names = {name for name, _, _ in rows}
    for name, state, status in rows:
        if state != "running":
            problems.append(Problem(f"container:{name}", f"Контейнер {name} не запущен ({status or state})"))
        elif "(unhealthy)" in status:
            problems.append(Problem(f"container:{name}", f"Контейнер {name} unhealthy ({status})"))
    for item in filter(None, (part.strip() for part in expected.split(","))):
        if item.endswith("*"):
            if not any(name.startswith(prefix + item[:-1]) for name in names):
                problems.append(Problem(f"container:{prefix}{item}", f"Нет ни одного контейнера {prefix}{item}"))
        elif prefix + item not in names:
            problems.append(Problem(f"container:{prefix}{item}", f"Контейнер {prefix}{item} отсутствует"))
    return problems


def heartbeat_problems(ages: dict[str, int | None], max_age: int = HEARTBEAT_MAX_AGE) -> list[Problem]:
    """ages maps service_kind -> seconds since the freshest heartbeat of that kind."""
    problems = []
    for kind in ("worker", "scheduler"):
        age = ages.get(kind)
        if age is None:
            problems.append(Problem(f"heartbeat:{kind}", f"Нет ни одного heartbeat от {kind}"))
        elif age > max_age:
            problems.append(Problem(f"heartbeat:{kind}", f"{kind} молчит {age // 60} мин (порог {max_age // 60} мин)"))
    return problems


def gmail_problem(age: int | None, max_age: int = GMAIL_MAX_AGE) -> Problem | None:
    # None means gmail.sync never completed: Gmail is not connected, nothing to watch.
    if age is not None and age > max_age:
        return Problem("gmail_sync", f"gmail.sync не завершался {age // 60} мин (порог {max_age // 60} мин)")
    return None


def health_problem(code: int | None, url: str = HEALTH_URL) -> Problem | None:
    if code != 200:
        return Problem("backend_health", f"Backend health {url} ответил {code if code is not None else 'ошибкой соединения'}")
    return None


def decide(state: dict, problems: list[Problem], now: float, *, repeat_after: int = REPEAT_AFTER,
           silenced: bool = False, min_age: int = MIN_PROBLEM_AGE) -> tuple[list[Problem], list[str], dict]:
    """Return (alerts to send, recovered keys, new state).

    state: {key: {"first_seen": ts, "last_alert": ts, "message": str}}.
    While silenced nothing is sent and last_alert is not advanced, so an
    unresolved problem is reported as soon as the silence ends.
    A problem alerts only after it persists for min_age seconds (disk:/memory:
    no delay) so a short deployment stop does not page anyone. A problem that
    clears before the delay is dropped from the state silently; "recovered" is
    sent only for keys that were actually alerted before.
    """
    alerts: list[Problem] = []
    new_state: dict = {}
    current = {problem.key: problem for problem in problems}
    for key, problem in current.items():
        entry = dict(state.get(key) or {"first_seen": now, "last_alert": None})
        entry["message"] = problem.message
        last = entry.get("last_alert")
        age = now - entry["first_seen"]
        delay = 0 if key.startswith("disk:") or key == "memory" else min_age
        if not silenced and age >= delay and (last is None or now - last >= repeat_after):
            alerts.append(problem)
            entry["last_alert"] = now
        new_state[key] = entry
    recovered = [] if silenced else [key for key, entry in state.items() if key not in current and entry.get("last_alert") is not None]
    return alerts, sorted(recovered), new_state


def format_alerts(alerts: list[Problem], host: str = HOST) -> str:
    lines = [f"🔴 PU Workspace watchdog ({host}):"] + [f"• {problem.message}" for problem in alerts]
    return "\n".join(lines)


def format_recovered(keys: list[str], state: dict, host: str = HOST) -> str:
    lines = [f"🟢 PU Workspace watchdog ({host}): восстановлено"]
    lines += [f"• {state.get(key, {}).get('message', key)}" for key in keys]
    return "\n".join(lines)


def parse_env_file(text: str) -> dict[str, str]:
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def parse_duration(text: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    text = text.strip().lower()
    if text and text[-1] in units:
        return int(float(text[:-1]) * units[text[-1]])
    return int(text)


# --------------------------------------------------------------------------- probes (read-only)


def run(cmd: list[str], timeout: int = 30, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)


def psql(sql: str) -> list[list[str]]:
    result = run(["docker", "exec", DB, "psql", "-U", os.getenv("WATCHDOG_DB_USER", "pu_user"),
                  "-d", os.getenv("WATCHDOG_DB_NAME", "pu_workspace"), "-v", "ON_ERROR_STOP=1",
                  "-qAtF", "|", "-c", "BEGIN READ ONLY", "-c", sql, "-c", "COMMIT"])
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "psql failed").strip()[:200])
    return [line.split("|") for line in result.stdout.splitlines() if line and line not in {"BEGIN", "COMMIT"}]


def collect() -> list[Problem]:
    problems: list[Problem] = []

    def enabled(name: str) -> bool:
        return name not in DISABLED

    if enabled("disk"):
        for path in DISK_PATHS:
            if not os.path.isdir(path):
                continue
            stat = os.statvfs(path)
            used = (stat.f_blocks - stat.f_bfree) * stat.f_frsize
            avail = stat.f_bavail * stat.f_frsize
            if used + avail:
                problem = disk_problem(path, used * 100.0 / (used + avail))
                if problem:
                    problems.append(problem)

    if enabled("memory"):
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, _, rest = line.partition(":")
            info[name] = int(rest.split()[0])
        problem = memory_problem(info.get("MemTotal", 0), info.get("MemAvailable", 0))
        if problem:
            problems.append(problem)

    if enabled("containers"):
        result = run(["docker", "ps", "-a", "--filter", f"name=^{PREFIX}",
                      "--format", "{{.Names}}|{{.State}}|{{.Status}}"])
        if result.returncode != 0:
            problems.append(Problem("docker", f"docker ps не отвечает: {result.stderr.strip()[:200]}"))
        else:
            rows = [tuple(line.split("|", 2)) for line in result.stdout.splitlines() if line.count("|") >= 2]
            problems.extend(container_problems(rows))

    if enabled("heartbeat") or enabled("gmail_sync"):
        try:
            if enabled("heartbeat"):
                rows = psql("select service_kind, extract(epoch from now() - max(last_seen))::bigint "
                            "from service_heartbeats where service_kind in ('worker','scheduler') group by 1")
                problems.extend(heartbeat_problems({kind: int(age) for kind, age in rows}))
            if enabled("gmail_sync"):
                rows = psql("select extract(epoch from now() - max(completed_at))::bigint from background_jobs "
                            "where kind = 'gmail.sync' and status = 'completed'")
                age = rows[0][0] if rows and rows[0] and rows[0][0] else None
                problem = gmail_problem(int(age) if age is not None else None)
                if problem:
                    problems.append(problem)
        except Exception as exc:  # DB down: the container check usually reports it too
            problems.append(Problem("database", f"Не удалось прочитать БД: {exc}"))

    if enabled("backend_health"):
        code = None
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=10) as response:
                code = response.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        except Exception:
            code = None
        problem = health_problem(code)
        if problem:
            problems.append(problem)

    return problems


# --------------------------------------------------------------------------- delivery


def backend_running() -> bool:
    result = run(["docker", "inspect", "-f", "{{.State.Running}}", BACKEND], timeout=15)
    return result.returncode == 0 and result.stdout.strip() == "true"


def send_via_backend(text: str) -> bool:
    code = ("import sys\nfrom app.integrations.telegram import notify_telegram\n"
            "sys.exit(0 if notify_telegram(sys.stdin.read()) else 1)")
    try:
        return run(["docker", "exec", "-i", BACKEND, "python", "-c", code], timeout=60, stdin=text).returncode == 0
    except Exception:
        return False


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 20) -> bool:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        # Never surface the exception: urllib errors may echo the URL with the token.
        return False


def send_direct(text: str) -> bool:
    try:
        env = parse_env_file(ENV_FILE.read_text(encoding="utf-8"))
    except OSError:
        return False
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    relay, secret = env.get("TELEGRAM_RELAY_URL", "").rstrip("/"), env.get("TELEGRAM_RELAY_SECRET", "")
    if relay and secret and _post_json(f"{relay}/send", {"chat_id": chat, "message": text[:4000]},
                                       {"X-Relay-Secret": secret}):
        return True
    return _post_json(f"https://api.telegram.org/bot{token}/sendMessage",
                      {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True})


def send(text: str) -> bool:
    if backend_running() and send_via_backend(text):
        return True
    return send_direct(text)


def delivery_plan() -> str:
    """Describe the channel without reading or printing secret values."""
    if backend_running():
        return f"docker exec {BACKEND} notify_telegram (fallback: Bot API via {ENV_FILE})"
    return f"Bot API directly with token/chat from {ENV_FILE} (backend container is down)"


# --------------------------------------------------------------------------- state


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def silenced_until(path: Path) -> float:
    try:
        return float(path.read_text().strip() or "inf")
    except FileNotFoundError:
        return 0.0
    except (OSError, ValueError):
        return float("inf")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="run checks, print what would be sent, write nothing")
    parser.add_argument("--silence", metavar="DURATION", help="mute alerts for e.g. 30m, 6h, 2d and exit")
    parser.add_argument("--unsilence", action="store_true", help="remove the mute and exit")
    parser.add_argument("--test-alert", action="store_true", help="send one test message and exit")
    args = parser.parse_args(argv)

    state_file, silence_file = STATE_DIR / "state.json", STATE_DIR / "silence_until"
    now = time.time()

    if args.silence:
        STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        until = now + parse_duration(args.silence)
        silence_file.write_text(str(int(until)))
        print("silenced until", time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(until)))
        return 0
    if args.unsilence:
        silence_file.unlink(missing_ok=True)
        print("silence removed")
        return 0
    if args.test_alert:
        ok = send(f"🧪 PU Workspace watchdog ({HOST}): тестовое сообщение")
        print("test alert sent" if ok else "test alert FAILED")
        return 0 if ok else 1

    problems = collect()
    state = load_state(state_file)
    silenced = silenced_until(silence_file) > now
    alerts, recovered, new_state = decide(state, problems, now, silenced=silenced)

    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(now))
    print(f"[{stamp}] problems={len(problems)} alerts={len(alerts)} recovered={len(recovered)} silenced={silenced}")
    for problem in problems:
        print(f"  problem {problem.key}: {problem.message}")

    alert_text = format_alerts(alerts) if alerts else None
    recovered_text = format_recovered(recovered, state) if recovered else None

    if args.dry_run:
        print(f"DRY RUN: nothing sent, state not written; channel would be: {delivery_plan()}")
        for message in filter(None, (alert_text, recovered_text)):
            print("--- would send:\n" + message)
        if not (alert_text or recovered_text):
            print("--- nothing to send")
        return 0

    # On a failed delivery keep the old state for those keys so the next run retries.
    if alert_text and not send(alert_text):
        print("WARNING: Telegram delivery of the alert failed", file=sys.stderr)
        for problem in alerts:
            new_state[problem.key]["last_alert"] = (state.get(problem.key) or {}).get("last_alert")
    if recovered_text and not send(recovered_text):
        print("WARNING: Telegram delivery of the recovery failed", file=sys.stderr)
        for key in recovered:
            new_state[key] = state[key]
    save_state(state_file, new_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
