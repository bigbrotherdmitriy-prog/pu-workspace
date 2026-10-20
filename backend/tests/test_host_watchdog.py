from pathlib import Path
import configparser
import importlib.util
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "scripts" / "monitoring"


def load_module():
    spec = importlib.util.spec_from_file_location("host_watchdog", SCRIPT_DIR / "host_watchdog.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["host_watchdog"] = module
    spec.loader.exec_module(module)
    return module


wd = load_module()
P = wd.Problem
HOUR = 3600


def test_shell_wrapper_and_python_are_syntactically_valid():
    subprocess.run(["sh", "-n", str(SCRIPT_DIR / "host_watchdog.sh")], check=True)
    compile((SCRIPT_DIR / "host_watchdog.py").read_text(encoding="utf-8"), "host_watchdog.py", "exec")


def test_disk_threshold_is_inclusive_at_80_percent():
    assert wd.disk_problem("/", 79.9) is None
    assert wd.disk_problem("/", 80.0).key == "disk:/"
    assert wd.disk_problem("/mnt/data", 95).key == "disk:/mnt/data"


def test_memory_alerts_below_10_percent_available():
    assert wd.memory_problem(4_000_000, 400_000) is None
    assert wd.memory_problem(4_000_000, 399_000).key == "memory"
    assert wd.memory_problem(0, 0) is None


def test_containers_not_running_unhealthy_or_missing():
    rows = [
        ("puw-primary-next-primary-backend", "running", "Up 6 hours (healthy)"),
        ("puw-primary-next-primary-db", "running", "Up 6 hours (unhealthy)"),
        ("puw-primary-next-scheduler-1", "exited", "Exited (1) 2 minutes ago"),
        ("puw-primary-next-worker-1", "running", "Up 6 hours"),
    ]
    keys = {p.key for p in wd.container_problems(rows)}
    assert keys == {
        "container:puw-primary-next-primary-db",
        "container:puw-primary-next-scheduler-1",
        "container:puw-primary-next-telegram-relay",
    }
    no_workers = [row for row in rows if "worker" not in row[0]]
    assert "container:puw-primary-next-worker-*" in {p.key for p in wd.container_problems(no_workers)}


def test_all_containers_healthy_is_quiet():
    rows = [(f"puw-primary-next-{name}", "running", "Up 1 hour") for name in
            ("primary-backend", "primary-db", "scheduler-1", "worker-1", "worker-2", "telegram-relay")]
    assert wd.container_problems(rows) == []


def test_heartbeat_uses_freshest_row_per_kind_and_5_minute_limit():
    assert wd.heartbeat_problems({"worker": 10, "scheduler": 300}) == []
    keys = {p.key for p in wd.heartbeat_problems({"worker": 301})}
    assert keys == {"heartbeat:worker", "heartbeat:scheduler"}


def test_gmail_sync_age_limit_and_never_connected():
    assert wd.gmail_problem(None) is None
    assert wd.gmail_problem(1800) is None
    assert wd.gmail_problem(1801).key == "gmail_sync"


def test_health_requires_200():
    assert wd.health_problem(200) is None
    assert wd.health_problem(502).key == "backend_health"
    assert wd.health_problem(None).key == "backend_health"


def test_new_problem_alerts_once_then_waits_6_hours():
    disk = P("disk:/", "disk full")
    alerts, recovered, state = wd.decide({}, [disk], now=1000)
    assert alerts == [disk] and recovered == []
    alerts, _, state = wd.decide(state, [disk], now=1000 + 6 * HOUR - 1)
    assert alerts == []
    assert state["disk:/"]["last_alert"] == 1000
    alerts, _, state = wd.decide(state, [disk], now=1000 + 6 * HOUR)
    assert alerts == [disk]
    assert state["disk:/"]["first_seen"] == 1000


def test_cleared_problem_reports_recovery_once():
    disk = P("disk:/", "disk full")
    _, _, state = wd.decide({}, [disk], now=0)
    alerts, recovered, state = wd.decide(state, [], now=300)
    assert alerts == [] and recovered == ["disk:/"] and state == {}
    assert wd.decide(state, [], now=600)[1] == []
    assert "восстановлено" in wd.format_recovered(recovered, {"disk:/": {"message": "disk full"}})


def test_independent_problems_are_deduped_separately():
    disk, mem = P("disk:/", "d"), P("memory", "m")
    _, _, state = wd.decide({}, [disk], now=0)
    alerts, _, _ = wd.decide(state, [disk, mem], now=60)
    assert alerts == [mem]


def test_silence_sends_nothing_and_alerts_after_silence_ends():
    disk = P("disk:/", "d")
    alerts, recovered, state = wd.decide({}, [disk], now=0, silenced=True)
    assert alerts == [] and state["disk:/"]["last_alert"] is None
    alerts, _, _ = wd.decide(state, [disk], now=60)
    assert alerts == [disk]
    # A problem that was never alerted produces no recovery message.
    assert wd.decide(state, [], now=60)[1] == []


def test_deployment_stop_shorter_than_min_age_does_not_alert():
    container = P("container:puw-primary-next-primary-db", "db down")
    alerts, recovered, state = wd.decide({}, [container], now=1000)
    assert alerts == [] and recovered == []
    assert state[container.key]["first_seen"] == 1000
    assert state[container.key]["last_alert"] is None
    # Still down but only 60s have passed.
    alerts, _, state = wd.decide(state, [container], now=1060)
    assert alerts == []


def test_same_problem_after_min_age_then_alerts():
    container = P("container:puw-primary-next-primary-db", "db down")
    _, _, state = wd.decide({}, [container], now=1000)
    alerts, _, state = wd.decide(state, [container], now=1060)
    assert alerts == []
    alerts, _, state = wd.decide(state, [container], now=1200)
    assert alerts == [container]
    assert state[container.key]["first_seen"] == 1000
    assert state[container.key]["last_alert"] == 1200


def test_problem_cleared_before_min_age_is_silent():
    container = P("container:puw-primary-next-worker-1", "worker down")
    _, _, state = wd.decide({}, [container], now=1000)
    alerts, recovered, state = wd.decide(state, [], now=1060)
    assert alerts == [] and recovered == [] and state == {}


def test_disk_and_memory_alert_immediately():
    disk, mem = P("disk:/", "d"), P("memory", "m")
    alerts, _, _ = wd.decide({}, [disk], now=1000)
    assert alerts == [disk]
    alerts, _, _ = wd.decide({}, [mem], now=1000)
    assert alerts == [mem]


def test_env_file_parsing_and_durations():
    env = wd.parse_env_file("# c\nexport TELEGRAM_BOT_TOKEN='a:b'\nTELEGRAM_CHAT_ID=\"42\"\nBAD\nX=1=2\n")
    assert env == {"TELEGRAM_BOT_TOKEN": "a:b", "TELEGRAM_CHAT_ID": "42", "X": "1=2"}
    assert wd.parse_duration("30m") == 1800 and wd.parse_duration("6h") == 21600 and wd.parse_duration("90") == 90


def test_dry_run_reads_but_never_writes_or_sends(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wd, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(wd, "collect", lambda: [P("memory", "мало памяти")])
    monkeypatch.setattr(wd, "delivery_plan", lambda: "test")
    sent = []
    monkeypatch.setattr(wd, "send", lambda text: sent.append(text) or True)
    assert wd.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "мало памяти" in out
    assert sent == [] and not (tmp_path / "state").exists()


def test_real_run_sends_and_keeps_state_for_retry_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, "STATE_DIR", tmp_path)
    monkeypatch.setattr(wd, "collect", lambda: [P("memory", "m")])
    monkeypatch.setattr(wd, "send", lambda text: False)
    wd.main([])
    assert wd.load_state(tmp_path / "state.json")["memory"]["last_alert"] is None
    sent = []
    monkeypatch.setattr(wd, "send", lambda text: sent.append(text) or True)
    wd.main([])
    wd.main([])
    assert len(sent) == 1


def test_main_does_not_alert_for_fresh_container_problem(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wd, "STATE_DIR", tmp_path)
    monkeypatch.setattr(wd, "collect", lambda: [P("container:puw-primary-next-primary-db", "db down")])
    sent = []
    monkeypatch.setattr(wd, "send", lambda text: sent.append(text) or True)
    assert wd.main([]) == 0
    assert sent == []
    state = wd.load_state(tmp_path / "state.json")
    assert state["container:puw-primary-next-primary-db"]["last_alert"] is None
    out = capsys.readouterr().out
    assert "alerts=0" in out


def test_secret_values_are_never_printed():
    source = (SCRIPT_DIR / "host_watchdog.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        if "print(" in line:
            assert "token" not in line.lower() and "secret" not in line.lower()


def test_systemd_units_run_every_5_minutes_as_oneshot():
    service = configparser.ConfigParser(strict=False, interpolation=None)
    service.read(SCRIPT_DIR / "systemd" / "pu-watchdog.service")
    assert service["Service"]["Type"] == "oneshot"
    assert service["Service"]["ExecStart"].endswith("/host_watchdog.sh")
    assert service["Service"]["StateDirectory"] == "pu-watchdog"
    timer = configparser.ConfigParser(strict=False, interpolation=None)
    timer.read(SCRIPT_DIR / "systemd" / "pu-watchdog.timer")
    assert timer["Timer"]["OnUnitActiveSec"] == "5min"
    assert timer["Timer"]["Unit"] == "pu-watchdog.service"
