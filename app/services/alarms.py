"""Alarm and anomaly engine. Distinguishes EVENT DETECTION from proven root cause: every alarm
states the evidence (rule, values, thresholds) and 'possible causes' for operator judgement.

Classes: blast flow deviation, sensor drift (reconciliation/innovation), top-gas excursion,
permeability deterioration, mass-balance inconsistency, thermal instability, tap deviations,
stave heat-flux increase, hanging/slipping proxy, data-quality loss, model envelope.
"""
from __future__ import annotations

import uuid
from collections import deque
from typing import Dict, List, Optional

import numpy as np

SEV_ORDER = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}


class AlarmEngine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.active: Dict[str, dict] = {}
        self.log: deque = deque(maxlen=1000)
        self.hist: Dict[str, deque] = {k: deque(maxlen=90) for k in ("dp", "hmt", "stave", "eta", "tg", "si", "stock")}

    def _raise(self, code, sev, msg, t_iso, evidence, causes):
        if code in self.active:
            a = self.active[code]
            a["last_seen"] = t_iso
            a["evidence"] = evidence
            a["severity"] = sev if SEV_ORDER[sev] > SEV_ORDER[a["severity"]] else a["severity"]
            return
        a = {"id": uuid.uuid4().hex, "code": code, "severity": sev, "message": msg, "first_seen": t_iso, "last_seen": t_iso,
             "evidence": evidence, "possible_causes": causes, "acknowledged_by": None, "acknowledged_at": None,
             "state": "ACTIVE", "classification": "event detection - root cause not proven"}
        self.active[code] = a
        self.log.append(dict(a))

    def _clear(self, code, t_iso):
        a = self.active.pop(code, None)
        if a:
            a = dict(a)
            a["state"] = "CLEARED"
            a["cleared"] = t_iso
            self.log.append(a)

    def evaluate(self, t_iso: str, k: Dict[str, dict], dq: dict, balance_ok: bool, observer: Optional[dict],
                 model_ok: bool, meas: Dict[str, float]) -> List[dict]:
        g = lambda n: (k.get(n) or {}).get("value")
        lim = {c["name"]: c for c in self.cfg["constraints"]}
        rules = []
        hmt, si, dp = g("hmt_C"), g("hm_si_pct"), g("pressure_drop_kPa")
        for key, v in (("dp", dp), ("hmt", hmt), ("si", si), ("eta", g("eta_CO_pct")), ("tg", g("top_gas_T_C"))):
            if v is not None:
                self.hist[key].append(v)
        st = meas.get("ST_Q_BOSH")
        if st is not None:
            self.hist["stave"].append(st)
        # thermal state
        L = lim.get("hmt_C", {})
        if hmt is not None and L:
            if hmt < L["low_alarm"]:
                rules.append(("THERMAL_LOW", "CRITICAL" if hmt < L["low_hard"] else "WARNING",
                              f"Hot metal temperature low ({hmt:.0f} degC)", {"hmt_C": hmt, "low_alarm": L["low_alarm"]},
                              ["coke-rate deficit", "high burden moisture", "increased heat losses", "raw burden descending low"]))
            elif hmt > L["high_alarm"]:
                rules.append(("THERMAL_HIGH", "WARNING", f"Hot metal temperature high ({hmt:.0f} degC)", {"hmt_C": hmt},
                              ["excess fuel rate", "high blast temperature / O2"]))
        if len(self.hist["hmt"]) > 20:
            arr = np.array(self.hist["hmt"])
            slope = np.polyfit(np.arange(len(arr)), arr, 1)[0] * 30  # degC per ~hour (2-min steps)
            if abs(slope) > 12:
                rules.append(("THERMAL_TREND", "WARNING", f"Rapid thermal trend ({slope:+.1f} degC/h)", {"slope_C_h": slope},
                              ["thermal instability", "operating change propagating"]))
        L = lim.get("pressure_drop_kPa", {})
        if dp is not None and L:
            if dp > L["high_alarm"]:
                rules.append(("PERMEABILITY", "CRITICAL" if dp > L["high_hard"] else "WARNING",
                              f"High pressure drop ({dp:.0f} kPa) - permeability deterioration",
                              {"dP_kPa": dp, "alarm": L["high_alarm"]}, ["fines in burden", "low cohesive zone", "high slag volume"]))
        if len(self.hist["dp"]) > 15:
            arr = np.array(self.hist["dp"])
            if arr[-1] - arr[:-5].mean() > 15:
                rules.append(("HANGING_RISK", "WARNING", "Pressure-drop surge (hanging/slip proxy)",
                              {"dp_now": float(arr[-1]), "dp_mean": float(arr[:-5].mean())},
                              ["burden hanging", "channeling", "sensor fault"]))
        si_L = lim.get("si_pct", {})
        if si is not None and si_L and (si < si_L["low_alarm"] or si > si_L["high_alarm"]):
            rules.append(("SI_DEVIATION", "WARNING", f"Hot metal Si out of window ({si:.2f} %)", {"si": si}, ["thermal state change"]))
        tg = g("top_gas_T_C")
        L = lim.get("top_gas_temp_C", {})
        if tg is not None and L and (tg > L["high_alarm"] or tg < L["low_alarm"]):
            rules.append(("TOP_GAS_EXCURSION", "WARNING", f"Top gas temperature excursion ({tg:.0f} degC)", {"T": tg},
                          ["central gas flow change", "burden distribution", "charging delay"]))
        if len(self.hist["stave"]) > 20:
            arr = np.array(self.hist["stave"])
            if arr[-1] > 1.25 * np.median(arr):
                rules.append(("STAVE_HEAT_FLUX", "WARNING", "Bosh stave heat load increase", {"kW": float(arr[-1])},
                              ["scab fall-off", "wall gas flow", "lining wear"]))
        lv_margin = g("hearth_liquid_margin_m")
        span = float(self.cfg["geometry"].get("taphole_to_tuyere_m", 3.0))
        if lv_margin is not None and lv_margin < 0.4 * span:          # liquids above 60 % of taphole->tuyere span
            rules.append(("HEARTH_LEVEL_HIGH", "CRITICAL" if lv_margin < 0.15 * span else "WARNING",
                          f"Hearth liquid level {lv_margin:.1f} m below the tuyeres", {"margin_m": lv_margin},
                          ["tapping delay / insufficient taphole capacity", "high slag volume", "taphole closed"]))
        if not dq.get("critical_ok", True):
            rules.append(("DATA_QUALITY", "CRITICAL" if dq.get("score", 100) < 60 else "WARNING",
                          f"Critical tags not GOOD: {', '.join(dq.get('critical_not_good', []))}", dq,
                          ["instrument failure", "communication loss", "calibration"]))
        if not balance_ok:
            rules.append(("BALANCE_FAIL", "CRITICAL", "Model conservation check failed", {}, ["numerical issue"]))
        if not model_ok:
            rules.append(("MODEL_ENVELOPE", "CRITICAL", "Physics model outside validated envelope", {}, ["extreme operating state"]))
        if observer and observer.get("mean_normalised_innovation") and observer["mean_normalised_innovation"] > 4:
            rules.append(("SENSOR_DRIFT_SUSPECT", "WARNING", "Observer innovations persistently large (drift/model mismatch)",
                          {"mean_NIS": observer["mean_normalised_innovation"]}, ["analyser drift", "thermocouple bias", "model mismatch"]))
        raised = set()
        for code, sev, msg, ev, causes in rules:
            self._raise(code, sev, msg, t_iso, ev, causes)
            raised.add(code)
        for code in list(self.active):
            if code not in raised:
                self._clear(code, t_iso)
        return list(self.active.values())

    def acknowledge(self, alarm_id: str, user: str, t_iso: str) -> Optional[dict]:
        for a in self.active.values():
            if a["id"] == alarm_id:
                a["acknowledged_by"] = user
                a["acknowledged_at"] = t_iso
                return a
        return None

    def critical_active(self) -> bool:
        return any(a["severity"] == "CRITICAL" for a in self.active.values())
