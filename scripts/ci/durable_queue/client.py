"""In-network probe CLI. Output is limited to synthetic states, never cookies."""
import json
import os
import sys
import httpx


def main():
    operation = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    api = args.pop("api", "api1")
    assert api in {"api1", "api2"}
    with httpx.Client(base_url=f"http://{api}:8000", trust_env=False, timeout=10) as client:
        if operation == "ready":
            for api in ("api1", "api2"):
                r = client.get(f"http://{api}:8000/api/readiness")
                assert r.status_code == 200 and r.json()["ready"] is True
            return {"ready": True}
        r = client.post("/auth/login", json={"email": "smoke@example.test", "password": os.environ["CI_SMOKE_PASSWORD"]})
        assert r.status_code == 200
        headers = {"X-CSRF-Token": client.cookies.get("pu_csrf")}
        if operation == "create":
            key = args.pop("key")
            headers["Idempotency-Key"] = key
            r = client.post("/ci/jobs", json=args, headers=headers)
        elif operation == "state":
            r = client.get(f"/ci/jobs/{int(args['id'])}")
        elif operation == "metrics":
            r = client.get("/admin/jobs/metrics")
        elif operation == "permissions":
            # No session: all three privileged operator actions must reject.
            for action in ("retry", "redrive", "cancel"):
                denied = httpx.post(f"http://api2:8000/admin/jobs/{int(args['id'])}/{action}", trust_env=False)
                assert denied.status_code == 401
            return {"anonymous_operator_rejected": True}
        elif operation == "permissions-member":
            for action in ("retry", "redrive", "cancel"):
                denied = client.post(f"/admin/jobs/{int(args['id'])}/{action}", headers=headers)
                assert denied.status_code == 403
            return {"member_operator_rejected": True}
        else:
            assert operation in {"cancel", "retry", "redrive"}
            r = client.post(f"/admin/jobs/{int(args['id'])}/{operation}", headers=headers)
        assert r.status_code == 200
        return r.json()


if __name__ == "__main__":
    try:
        print(json.dumps(main()))
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__}))
        raise SystemExit(1)
