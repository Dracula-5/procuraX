"""End-to-end check of the local observability stack: drive traffic, then ask Prometheus, Grafana and Jaeger.

    PROCURAX_OTEL_ENABLED=true docker compose -f docker-compose.stack.yml --profile observability up -d --build
    uv run python scripts/observability_check.py --report ../reports/observability_check.json

Registers a throw-away tenant through the web container (nginx -> API), raises and submits a request, makes
one failed sign-in, waits two scrape intervals, then checks: both scrape targets up, HTTP and business
series present, the alert rules loaded, the Grafana dashboard provisioned and answering a query through its
Prometheus datasource, and traces (with SQL spans) in Jaeger. Exit code 1 if a check fails.
"""

import argparse
import json
import secrets
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

PASSWORD = "Str0ngPassw0rd1"  # noqa: S105 - a throw-away tenant on a local stack


def main() -> int:
    parser = argparse.ArgumentParser(description="ProcuraX observability stack check")
    parser.add_argument("--web", default="http://localhost:8088")
    parser.add_argument("--prometheus", default="http://localhost:9090")
    parser.add_argument("--grafana", default="http://localhost:3001")
    parser.add_argument("--jaeger", default="http://localhost:16686")
    parser.add_argument("--report")
    args = parser.parse_args()
    web, prom_url, grafana, jaeger = args.web, args.prometheus, args.grafana, args.jaeger
    results: dict[str, object] = {"checked_at": datetime.now(UTC).isoformat(timespec="seconds")}

    with httpx.Client(timeout=30) as c:
        email = f"obs-{secrets.token_hex(4)}@example.com"
        tenant = {"organization_name": "Observability Check K.K.", "full_name": "Ops Admin", "email": email}
        r = c.post(f"{web}/api/v1/auth/register", json={**tenant, "password": PASSWORD})
        r.raise_for_status()
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        cost_centre = c.get(f"{web}/api/v1/cost-centers", headers=headers).json()[0]["id"]
        request = {
            "title": "Monitors",
            "category": "hardware",
            "cost_center_id": cost_centre,
            "justification": "Desk setup",
            "items": [{"description": "Monitor", "quantity": "2", "unit_price": "40000"}],
        }
        pr = c.post(f"{web}/api/v1/purchase-requests", headers=headers, json=request).json()
        results["submitted_status"] = c.patch(
            f"{web}/api/v1/purchase-requests/{pr['id']}/submit", headers=headers
        ).status_code
        for _ in range(3):
            c.get(f"{web}/api/v1/purchase-requests", headers=headers)
        bad = c.post(f"{web}/api/v1/auth/login", json={"email": email, "password": "wrong-password-1"})
        results["bad_login_status"] = bad.status_code
        results["metrics_without_token_via_web"] = c.get(f"{web}/api/v1/metrics").status_code

        time.sleep(35)  # two scrape intervals and a few worker cycles

        def prom(query: str) -> list:
            return c.get(f"{prom_url}/api/v1/query", params={"query": query}).json()["data"]["result"]

        def by(label: str, query: str) -> dict:
            return {s["metric"].get(label): s["value"][1] for s in prom(query)}

        results["prometheus_up"] = by("job", "up")
        results["http_series"] = len(prom("procurax_http_requests_total"))
        results["policy_outcomes"] = by("outcome", "procurax_policy_evaluations_total")
        results["auth_failures"] = by("kind", "procurax_auth_failures_total")
        results["worker_cycles_ok"] = by("job", 'procurax_worker_cycles_total{result="ok"}')
        results["outbox_pending"] = by("job", "procurax_outbox_pending_events")
        rules = c.get(f"{prom_url}/api/v1/rules").json()["data"]["groups"]
        results["alert_rules_loaded"] = sum(len(g["rules"]) for g in rules)

        results["grafana_dashboards"] = [
            d["title"] for d in c.get(f"{grafana}/api/search", params={"query": "ProcuraX"}).json()
        ]
        dashboard = c.get(f"{grafana}/api/dashboards/uid/procurax-overview").json()
        results["grafana_panels"] = len(dashboard["dashboard"]["panels"])
        query = {
            "queries": [
                {"refId": "A", "datasource": {"uid": "prometheus"}, "expr": "sum(procurax_http_requests_total)", "instant": True}
            ],
            "from": "now-5m",
            "to": "now",
        }
        answer = c.post(f"{grafana}/api/ds/query", json=query)
        frames = answer.json().get("results", {}).get("A", {}).get("frames", [])
        results["grafana_query_has_data"] = answer.status_code == 200 and bool(
            frames and frames[0].get("data", {}).get("values")
        )

        def traces(service: str, limit: int) -> list:
            params = {"service": service, "limit": limit, "lookback": "1h"}
            return c.get(f"{jaeger}/api/traces", params=params).json().get("data") or []

        results["jaeger_services"] = sorted(c.get(f"{jaeger}/api/services").json().get("data") or [])
        api_traces = traces("procurax-api", 20)
        results["jaeger_api_traces"] = len(api_traces)
        results["jaeger_worker_traces"] = len(traces("procurax-worker", 5))
        operations = {span["operationName"] for t in api_traces for span in t["spans"]}
        results["jaeger_has_sql_spans"] = any(o.startswith(("SELECT", "INSERT", "UPDATE")) for o in operations)

    up = results["prometheus_up"]
    assert isinstance(up, dict)
    passed = (
        up.get("procurax-api") == "1"
        and up.get("procurax-worker") == "1"
        and bool(results["http_series"])
        and results["alert_rules_loaded"] == 8
        and "ProcuraX overview" in results["grafana_dashboards"]  # type: ignore[operator]
        and bool(results["grafana_query_has_data"])
        and bool(results["jaeger_api_traces"])
        and bool(results["jaeger_has_sql_spans"])
    )
    results["passed"] = passed
    body = json.dumps(results, indent=2)
    print(body)
    if args.report:
        Path(args.report).write_text(body + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
