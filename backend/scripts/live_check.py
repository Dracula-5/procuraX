"""End-to-end check of a live ProcuraX deployment through its public API (fictional demo tenant).

    python scripts/live_check.py --api https://procurax-mod8.onrender.com --web https://procurax.netlify.app

Creates one purchase request in the demo tenant (reset nightly). Prints PASS/FAIL per check and exits 1
if any check fails.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

_args = argparse.ArgumentParser(description="Live deployment check")
_args.add_argument("--api", default="https://procurax-mod8.onrender.com")
_args.add_argument("--web", default="https://procurax.netlify.app")
_args.add_argument("--output", default=None, help="optional JSON results file")
ARGS = _args.parse_args()
API = ARGS.api.rstrip("/") + "/api/v1"
WEB = ARGS.web.rstrip("/")
results: list[tuple[str, bool, str]] = []


def call(method, path, body=None, token=None, origin=WEB):
    headers = {"Content-Type": "application/json", "Origin": origin}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        API + path,
        method=method,
        headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read()
            status, hdrs = response.status, dict(response.headers)
    except urllib.error.HTTPError as error:
        raw, status, hdrs = error.read(), error.code, dict(error.headers)
    ms = (time.perf_counter() - start) * 1000
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = raw.decode(errors="replace")
    return status, payload, hdrs, ms


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" ({detail})" if detail else ""))


status, personas, hdrs, ms = call("GET", "/auth/demo-personas")
check(
    "demo personas listed",
    status == 200 and len(personas) == 12,
    f"{len(personas) if isinstance(personas, list) else personas}",
)
check(
    "CORS allows the Netlify origin",
    hdrs.get("access-control-allow-origin") == WEB,
    hdrs.get("access-control-allow-origin"),
)
by_key = {p["email"].split("@")[0].replace(".", "_"): p["email"] for p in personas}


def login(key):
    status, body, _, _ = call("POST", "/auth/demo-login", {"email": by_key[key]})
    assert status == 200, body
    return body["access_token"]


engineer, manager, buyer, analyst, admin = (
    login(k) for k in ("it_engineer", "it_manager", "buyer", "analyst", "admin")
)
check("persona sign-in (5 roles)", True)

status, me, _, _ = call("GET", "/auth/me", token=buyer)
check(
    "/auth/me as procurement officer",
    status == 200 and "procurement_officer" in me["roles"],
    me.get("full_name"),
)

status, ccs, _, _ = call("GET", "/cost-centers", token=engineer)
_, vendors, _, _ = call("GET", "/vendors?status=approved&limit=50", token=buyer)
vendor = next(v for v in vendors["items"] if "hardware" in v["categories"])
it_cc = next((c for c in ccs if "IT" in c.get("code", "") or "IT" in c.get("name", "")), ccs[0])
status, created, _, ms = call(
    "POST",
    "/purchase-requests",
    {
        "title": "Live check: spare network switch",
        "category": "hardware",
        "cost_center_id": it_cc["id"],
        "justification": "End-to-end verification of the deployed workflow (demo tenant)",
        "preferred_vendor_id": vendor["id"],
        "items": [{"description": "24-port switch", "quantity": "1", "unit_price": "80000"}],
    },
    token=engineer,
)
check(
    "create purchase request",
    status == 201,
    f"{created.get('number') if isinstance(created, dict) else created} in {ms:.0f} ms",
)
status, submitted, _, _ = call("PATCH", f"/purchase-requests/{created['id']}/submit", token=engineer)
check(
    "submit with policy evaluation",
    status == 200,
    submitted.get("status") if isinstance(submitted, dict) else submitted,
)
if submitted.get("status") == "pending_approval":
    status, decided, _, _ = call("POST", f"/purchase-requests/{created['id']}/approve", token=manager)
    check("manager approval", status == 200, decided.get("status") if isinstance(decided, dict) else decided)

status, analytics, _, ms = call("GET", "/spend-analytics", token=analyst)
check(
    "spend analytics as analyst", status == 200 and "approved_spend_by_category" in analytics, f"{ms:.0f} ms"
)
status, denied, _, _ = call("GET", "/spend-analytics", token=engineer)
check("RBAC: employee denied analytics", status == 403, str(status))
status, ask, _, _ = call(
    "POST", "/procurement-assistant/query", {"question": "Show unmatched invoices"}, token=analyst
)
check(
    "assistant fixed-intent query", status == 200, (ask or {}).get("intent") if isinstance(ask, dict) else ask
)
status, kb, _, _ = call(
    "POST",
    "/procurement-assistant/knowledge",
    {"question": "見積は何社から取得が必要ですか quote"},
    token=admin,
)
check(
    "knowledge search (Japanese + English)",
    status == 200,
    f"{len(kb.get('citations', []))} citations" if isinstance(kb, dict) else kb,
)
status, decisions, _, _ = call("GET", "/decision-support/sourcing", token=analyst)
check(
    "decision-support report",
    status == 200,
    f"awards {decisions.get('awards_with_recommendation') if isinstance(decisions, dict) else decisions}",
)
status, reg, _, _ = call(
    "POST",
    "/auth/register",
    {
        "organization_name": "Visitor Trading K.K.",
        "full_name": "Visitor Example",
        "email": "visitor@example.com",
        "password": "Str0ngPassw0rd1",
    },
)
check("public demo blocks self-registration", status == 403, str(status))

print(json.dumps({"passed": sum(r[1] for r in results), "total": len(results)}))
if ARGS.output:
    with open(ARGS.output, "w", encoding="utf-8") as fh:
        json.dump(
            [{"check": n, "passed": ok, "detail": d} for n, ok, d in results],
            fh,
            ensure_ascii=False,
            indent=2,
        )
sys.exit(0 if all(r[1] for r in results) else 1)
