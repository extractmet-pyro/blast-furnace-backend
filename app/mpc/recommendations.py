"""Advisory recommendation lifecycle (ADVISORY ONLY - nothing is ever written to the plant).

States: PROPOSED -> ACKNOWLEDGED | REJECTED | EXPIRED, or ADVICE_SUPPRESSED / NO_FEASIBLE_SOLUTION
at creation. Acknowledgement records operator decision and comment in the audit trail only.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np

from .predictions import CV_DEFS, MV_DEFS

VALIDITY_MIN = 30


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def explain(moves: Dict[str, List[float]], gain: np.ndarray, mvs: List[str], cvs: List[str],
            scales: Optional[Dict[str, float]] = None) -> List[str]:
    """Plain-language summary. A total move below 5 % of the lever's engineering scale is reported as
    'hold' - an operator would not act on it and it only adds noise to the advice."""
    lines, held = [], []
    for mv, du in moves.items():
        tot = float(np.sum(du))
        if abs(tot) < 1e-9 or (scales and mv in scales and abs(tot) < 0.05 * scales[mv]):
            held.append(MV_DEFS[mv]["label"])
            continue
        j = mvs.index(mv)
        eff = []
        for c in ("hmt_C", "hm_si_pct", "raft_C", "fuel_rate_kg_tHM", "eta_CO_pct", "pressure_drop_kPa"):
            i = cvs.index(c)
            dy = gain[i, j] * tot
            if abs(dy) > 1e-3 * CV_DEFS[c]["scale"] * 10:
                eff.append(f"{CV_DEFS[c]['label']} {dy:+.2f} {CV_DEFS[c]['unit']}")
        lines.append(f"{'Increase' if tot > 0 else 'Decrease'} {MV_DEFS[mv]['label']} by {abs(tot):.2f} {MV_DEFS[mv]['unit']} "
                     f"(first move {du[0]:+.2f} now)" + (f"; expected steady effect: {', '.join(eff)}" if eff else ""))
    if not lines:
        lines.append("Hold current settings: predicted trajectory already satisfies targets within tolerance.")
    elif held:
        lines.append(f"Hold (change below 5 % of its engineering scale): {', '.join(held)}.")
    return lines


def build_recommendation(status: str, mode: str, payload: Dict[str, object], reasons: Optional[List[str]] = None) -> dict:
    now = utcnow()
    rec = {"id": uuid.uuid4().hex, "created_utc": iso(now), "expires_utc": iso(now + timedelta(minutes=VALIDITY_MIN)),
           "status": status, "mode": mode, "advisory_only": True, "plant_write": "NONE - recommendation only",
           "requires_approval": True, "reasons": reasons or [], **payload, "audit": []}
    if status in ("ADVICE_SUPPRESSED", "NO_FEASIBLE_SOLUTION", "TIMEOUT", "NO_ACTIVE_MV"):
        rec["recommended_action"] = "NO NEW MOVE - keep current setpoints and follow standard operating procedure."
    return rec


def acknowledge(rec: dict, user: str, role: str, decision: str, comment: str = "") -> dict:
    if decision not in ("ACKNOWLEDGED", "REJECTED"):
        raise ValueError("decision must be ACKNOWLEDGED or REJECTED")
    if rec["status"] != "PROPOSED":
        raise ValueError(f"Recommendation in state {rec['status']} cannot be acknowledged")
    if datetime.fromisoformat(rec["expires_utc"].replace("Z", "+00:00")) < utcnow():
        rec["status"] = "EXPIRED"
        raise ValueError("Recommendation expired")
    rec["status"] = decision
    rec["audit"].append({"at_utc": iso(utcnow()), "user": user, "role": role, "decision": decision, "comment": comment,
                         "effect": "audit state only - no plant write"})
    return rec
