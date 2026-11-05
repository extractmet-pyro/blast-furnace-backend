"""Generate docs/API_REFERENCE.md from the live FastAPI application (OpenAPI + route dependencies).

Usage:  python scripts/gen_api_reference.py
The minimum role of each endpoint is read from its dependency chain (require("<ROLE>")), so the table
cannot drift from the code.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SIM_AUTOSTART", "false")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/doc.db")

from fastapi.routing import APIRoute, APIWebSocketRoute  # noqa: E402

from app.main import app  # noqa: E402

ROLE_ORDER = ["VIEWER", "OPERATOR", "METALLURGIST", "PROCESS_ENGINEER", "ADMIN"]


def _roles(dependant) -> set:
    found = set()
    for d in dependant.dependencies:
        fn = d.call
        role = getattr(fn, "required_role", None)
        if role:
            found.add(role)
        if getattr(fn, "__name__", "") == "edge_principal":
            found.add("SERVICE_INGEST (X-Edge-Token)")
        found |= _roles(d)
    return found


def main() -> None:
    spec = app.openapi()
    rows = []
    from app.api.v1 import control, forecasts, physics, reports, simulations, status, telemetry
    from app.core.config import API_VERSION
    routes = [r for r in app.routes if isinstance(r, (APIRoute, APIWebSocketRoute))]
    for mod in (status, telemetry, physics, simulations, forecasts, control, reports):
        for r in mod.router.routes:          # FastAPI >= 0.16x keeps included routers lazily
            r.path = f"/api/{API_VERSION}{r.path}" if not r.path.startswith("/api/") else r.path
            routes.append(r)
    for r in routes:
        if isinstance(r, APIRoute):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                roles = _roles(r.dependant)
                ranked = [x for x in ROLE_ORDER if x in roles]
                role = ranked[-1] if ranked else (", ".join(sorted(roles)) if roles else "public")
                tag = (r.tags or ["service"])[0]
                summary = r.summary or (spec["paths"].get(r.path, {}).get(m.lower(), {}).get("summary") or "")
                rows.append((tag, r.path, m, role, summary, r.status_code or 200))
        elif isinstance(r, APIWebSocketRoute):
            rows.append(("telemetry", r.path, "WS", "VIEWER (token query, or anonymous viewer if enabled)",
                         "Live event stream: tick / event / heartbeat / resync, with sequence numbers", 101))
    rows.sort(key=lambda x: (x[0], x[1], x[2]))
    out = ["# API reference", "",
           f"Generated from the running application (`python scripts/gen_api_reference.py`), API version "
           f"{spec['info']['version']}. Interactive documentation: `/docs` (Swagger UI) and `/redoc` on the backend.", "",
           "Conventions",
           "- Base path `/api/v1`; JSON bodies; UTC ISO-8601 timestamps with `Z`.",
           "- Units are in the key name (`hmt_C`, `fuel_rate_kg_tHM`) or in a `unit` field next to the value.",
           "- Every value carries a `provenance` of MEASURED, MODEL, HYBRID or DERIVED where it matters.",
           "- Errors: `{\"error\": {\"code\", \"message\", \"details\", \"request_id\"}}` with HTTP 4xx/5xx.",
           "- Long work (simulations, studies, identification, training, NMPC) returns **202** with a job id; poll "
           "`GET /api/v1/simulations/{id}` and fetch `GET /api/v1/simulations/{id}/results`. A full queue answers "
           "**503** `QUEUE_FULL`; a job that waits or runs too long ends `FAILED` with a timeout message "
           "(`JOB_QUEUE_TIMEOUT_S`, `JOB_TIMEOUT_S`).",
           "- Too many requests from one address: **429** `RATE_LIMITED` with `Retry-After` (`RATE_LIMIT_PER_MIN`).",
           "- Authentication: `POST /api/v1/auth/login` returns a bearer token (JWT). Roles are hierarchical: "
           "VIEWER < OPERATOR < METALLURGIST < PROCESS_ENGINEER < ADMIN.",
           "- There is no endpoint that writes to a plant system. Acknowledging a recommendation changes audit state only.", ""]
    cur = None
    for tag, path, m, role, summary, code in rows:
        if tag != cur:
            out += ["", f"## {tag}", "", "| Method | Path | Minimum role | Success | Summary |", "|---|---|---|---|---|"]
            cur = tag
        out.append(f"| {m} | `{path}` | {role} | {code} | {summary.replace('|', '/')} |")
    out += ["", "## Examples", "",
            "```bash",
            "API=https://<backend>.onrender.com",
            "# sign in (demo account; the password is the backend's DEMO_USER_PASSWORD environment variable)",
            "TOKEN=$(curl -s -X POST $API/api/v1/auth/login -H 'Content-Type: application/json' \\",
            "        -d '{\"username\":\"operator\",\"password\":\"'$DEMO_USER_PASSWORD'\"}' | python -c 'import sys,json;print(json.load(sys.stdin)[\"access_token\"])')",
            "curl -s $API/api/v1/system/status",
            "curl -s $API/api/v1/twin/state -H \"Authorization: Bearer $TOKEN\"",
            "# advisory recommendation (audit-only, never transmitted)",
            "curl -s -X POST $API/api/v1/mpc/recommend -H \"Authorization: Bearer $TOKEN\" -H 'Content-Type: application/json' \\",
            "     -d '{\"targets\":{\"hmt_C\":1515}}'",
            "# what-if scenario as a job (METALLURGIST or higher)",
            "curl -s -X POST $API/api/v1/simulations -H \"Authorization: Bearer $TOKEN\" -H 'Content-Type: application/json' \\",
            "     -d '{\"scenario_id\":\"S03\"}'",
            "# export the current balance with provenance",
            "curl -s \"$API/api/v1/reports/export?kind=balance&format=xlsx\" -H \"Authorization: Bearer $TOKEN\" -o balance.xlsx",
            "```", ""]
    (ROOT / "docs" / "API_REFERENCE.md").write_text("\n".join(out))
    print(f"wrote {len(rows)} operations")


if __name__ == "__main__":
    main()
