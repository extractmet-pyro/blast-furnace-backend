"""Data-quality assessment: range, rate-of-change, staleness, flat-line, Hampel outlier,
calibration age and lab-sample handling. Raw values are never modified here - quality flags and
reasons are attached and the original value is preserved for the audit trail.

Quality states: GOOD | SUSPECT | BAD | MISSING | UNAVAILABLE (tag not installed).
Laboratory results are never forward-filled as if continuously measured; they carry their
sampling timestamp and an 'age' so consumers can apply proper lags.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np

from .tag_mapping import TAG_INDEX, TAGS


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# tags that must be GOOD for the advice trust gate (double weight in the score)
CRITICAL_TAGS = frozenset({"BL_FLOW", "BL_TEMP", "BL_PRESS", "TG_CO", "TG_CO2", "TG_TEMP", "TG_PRESS", "PCI_RATE", "BL_O2"})


class QualityEngine:
    def __init__(self, window: int = 31):
        self.hist: Dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
        self.last: Dict[str, dict] = {}
        self.last_good_ts: Dict[str, float] = {}
        # tags the active data source cannot provide (e.g. radial probes with a 1-D demo plant): reported
        # as UNAVAILABLE with the reason, and excluded from the score - never shown as zero or as MISSING
        self.not_provided: Dict[str, str] = {}

    def assess(self, readings: List[dict], now_iso: str) -> List[dict]:
        now = _ts(now_iso)
        out = []
        seen = set()
        for r in readings:
            tag = r["tag"]
            seen.add(tag)
            td = TAG_INDEX.get(tag)
            q, reasons = "GOOD", []
            v = r.get("value")
            if td is None:
                q, reasons = "SUSPECT", ["unknown tag (not in catalogue)"]
            elif v is None or (isinstance(v, float) and not np.isfinite(v)):
                q, reasons = "MISSING", ["no value"]
            else:
                if td.lo is not None and v < td.lo or td.hi is not None and v > td.hi:
                    q, reasons = "BAD", [f"outside physical range [{td.lo}, {td.hi}] {td.unit}"]
                h = self.hist[tag]
                prev = self.last.get(tag)
                if q == "GOOD" and prev is not None and prev.get("value") is not None and td.roc_max_per_min:
                    dt_min = max((_ts(r["ts_sensor"]) - _ts(prev["ts_sensor"])) / 60.0, 1e-6)
                    if abs(v - prev["value"]) / dt_min > td.roc_max_per_min:
                        q, reasons = "SUSPECT", [f"rate of change > {td.roc_max_per_min} {td.unit}/min"]
                if q == "GOOD" and len(h) >= 7 and td.signal_type == "ANALOG":
                    arr = np.array(h)
                    med = float(np.median(arr))
                    mad = float(np.median(np.abs(arr - med))) * 1.4826
                    if mad > 1e-9 and abs(v - med) > 4.0 * mad + 3 * td.noise_sd:
                        q, reasons = "SUSPECT", ["Hampel outlier (|x-median| > 4 MAD)"]
                    if len(h) >= h.maxlen - 1 and np.ptp(arr) < 1e-9 and td.noise_sd > 0:
                        q, reasons = "SUSPECT", ["flat-line (possible stuck sensor)"]
                if q != "BAD" and v is not None:
                    h.append(v)
            if td is not None and td.signal_type == "LAB":
                reasons.append(f"lab result sampled {r['ts_sensor']}, received {r['ts_ingest']}")
            rec = {**r, "quality": q, "reasons": reasons}
            if q == "GOOD":
                self.last_good_ts[tag] = _ts(r["ts_sensor"])
            self.last[tag] = rec
            out.append(rec)
        return out

    def snapshot(self, now_iso: str) -> List[dict]:
        now = _ts(now_iso)
        rows = []
        for td in TAGS:
            last = self.last.get(td.tag)
            if not td.installed or td.tag in self.not_provided:
                rows.append({"tag": td.tag, "quality": "UNAVAILABLE", "value": None, "unit": td.unit, "family": td.family,
                             "reasons": [self.not_provided.get(td.tag, "not installed at this site (never shown as zero)")]})
                continue
            if last is None and td.signal_type == "EVENT":
                rows.append({"tag": td.tag, "quality": "NO_EVENT", "value": None, "unit": td.unit, "family": td.family,
                             "signal_type": td.signal_type, "reasons": ["no event since start of data (normal for event tags)"]})
                continue
            if last is None:
                rows.append({"tag": td.tag, "quality": "MISSING", "value": None, "unit": td.unit, "family": td.family,
                             "reasons": ["no data received yet"]})
                continue
            age = now - _ts(last["ts_sensor"])
            q = last["quality"]
            reasons = list(last.get("reasons", []))
            if age > td.stale_timeout_s and td.signal_type in ("ANALOG", "LAB"):
                q = "SUSPECT" if q == "GOOD" else q
                reasons.append(f"stale: last sample {age / 60:.0f} min old (timeout {td.stale_timeout_s / 60:.0f} min)")
            rows.append({"tag": td.tag, "value": last.get("value"), "unit": td.unit, "family": td.family, "quality": q,
                         "age_s": age, "ts_sensor": last["ts_sensor"], "signal_type": td.signal_type, "reasons": reasons})
        return rows

    def score(self, now_iso: str) -> Dict[str, object]:
        snap = [r for r in self.snapshot(now_iso) if r["quality"] not in ("UNAVAILABLE", "NO_EVENT")]
        crit = CRITICAL_TAGS
        w = {"GOOD": 1.0, "SUSPECT": 0.5, "BAD": 0.0, "MISSING": 0.0}
        if not snap:
            return {"score": 0.0, "critical_ok": False, "counts": {}}
        tot = sum(w.get(r["quality"], 0) * (2.0 if r["tag"] in crit else 1.0) for r in snap)
        den = sum(2.0 if r["tag"] in crit else 1.0 for r in snap)
        counts: Dict[str, int] = {}
        for r in snap:
            counts[r["quality"]] = counts.get(r["quality"], 0) + 1
        crit_ok = all(r["quality"] in ("GOOD",) for r in snap if r["tag"] in crit)
        bad_crit = [r["tag"] for r in snap if r["tag"] in crit and r["quality"] != "GOOD"]
        return {"score": round(100.0 * tot / den, 1), "critical_ok": crit_ok, "critical_not_good": bad_crit, "counts": counts}
