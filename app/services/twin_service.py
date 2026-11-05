"""Runtime orchestrator.

    synthetic plant | historical replay | read-only edge batches
        -> data-quality engine -> physics twin (driven by measured MVs) -> parameter EKF
        -> virtual sensors / hybrid forecasts -> alarms -> advisory MPC (shadow) -> event stream

Thread model: one live-loop thread advances data time; API handlers read consistent snapshots
under a re-entrant lock; heavy work (scenarios, ROM identification, NMPC, training) runs in the
JobManager on CLONES of the twin so the live state is never mutated by a what-if.

Clocks: `data_t` is the data clock (seconds since EPOCH of the data source: simulated plant time,
replay-file time or edge-gateway time). All observer/forecast alignment uses the data clock.
Freshness additionally checks the wall clock, so a stalled loop is reported as stale.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
import logging
# pickle is used only for the service's own snapshots, SHA-256 checked before loading
import pickle  # nosec B403
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..core.config import APP_VERSION, MODEL_VERSION, get_settings
from ..core.errors import DomainError
from ..core.safety import MODE_INFO, Mode, SafetyPolicy
from ..data.quality import QualityEngine
from ..data.synthetic import EPOCH, FaultSpec, SyntheticPlant, iso, truth_config
from ..data.tag_mapping import TAG_INDEX, TAGS, TagMapper
from ..db.models import (Alarm, AuditEvent, ForecastRun, MPCRecommendation, ObserverSnapshot, Plant, PlantConfigVersion,
                         RawSample, RecommendationAcknowledgment)
from ..db.session import session
from ..estimation.observer import ParameterEKF, compute_sensitivities
from ..estimation.reconciliation import bf_reconciliation
from ..ml.features import live_row
from ..ml.registry import ModelRegistry, file_sha256
from ..mpc.constraints import build_constraints
from ..mpc.optimizer import MPCProblem, solve_linear_mpc, solve_nmpc_physics
from ..mpc.predictions import DMCPredictor, StepResponseModel, mv_scales, read_cvs
from ..mpc.recommendations import acknowledge, build_recommendation, explain
from ..mpc.verifier import trust_gate, verify_on_physics
from ..physics import outputs as out
from ..physics.defaults import build_config
from ..physics.integrator import BlastFurnaceModel
from ..physics.zones import geometry_payload
from .alarms import AlarmEngine
from .jobs import JobManager, _json_safe
from ..physics.material_balance import max_rel_residual

log = logging.getLogger("bf.twin")
DEMO_DIR = Path(__file__).resolve().parents[2] / "data" / "demo"
SHIPPED_MODELS = Path(__file__).resolve().parents[2] / "data" / "models"
STREAM_KPIS = ["production_t_d", "hmt_C", "hm_si_pct", "hm_s_pct", "fuel_rate_kg_tHM", "coke_rate_kg_tHM", "pci_rate_kg_tHM",
               "eta_CO_pct", "top_gas_T_C", "pressure_drop_kPa", "blast_pressure_kPa_g", "raft_C", "slag_rate_kg_tHM",
               "slag_B2", "dripping_zone_height_m", "heat_loss_total_MW", "active_coke_T_C", "productivity_t_m3_d",
               "hm_c_pct", "slag_FeO_pct", "top_CO_pct", "top_CO2_pct", "top_H2_pct", "solution_loss_kgC_tHM",
               "hearth_metal_level_m", "hearth_slag_level_m", "blast_volume_Nm3_min", "blast_temp_C",
               "o2_enrichment_pct", "pci_t_h", "blast_moisture_g_Nm3", "thermal_reserve_T_C"]
SETPOINT_KEYS = {"blast_volume_Nm3_min", "blast_temp_C", "blast_moisture_g_Nm3", "o2_enrichment_pct", "pci_t_h",
                 "top_pressure_kPa_g", "coke_batch_t", "ore_batch_t", "n_tuyeres_closed", "ng_Nm3_h", "h2_Nm3_h",
                 "burden_mix", "flux_kg_per_charge"}


def cfg_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()


# sections that never change the physical state (targets, limits, prices, labels): editing them must not
# invalidate a shipped initial state or identified ROM
NON_PHYSICS_KEYS = ("plant", "targets", "constraints", "economics", "carbon", "equipment_limits", "rate_limits_per_15min",
                    "mpc_default_available")


def _canon(o):
    """Canonical form for hashing: 11 and 11.0 are the same number (a browser's JSON.stringify writes 11.0 as 11)."""
    if o is None or isinstance(o, (bool, str)):
        return o
    if isinstance(o, (int, float)):
        f = float(o)
        return f if math.isfinite(f) else str(f)
    if isinstance(o, dict):
        return {str(k): _canon(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_canon(v) for v in o]
    return str(o)


def physics_hash(cfg: dict) -> str:
    """Hash of the sections that determine the physical state; insensitive to number formatting."""
    core = {k: _canon(v) for k, v in cfg.items() if k not in NON_PHYSICS_KEYS}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()


def _artefact_matches(meta: dict, cfg: dict) -> bool:
    if meta.get("physics_sha256"):
        return meta["physics_sha256"] == physics_hash(cfg)
    return meta.get("cfg_sha256") == cfg_hash(cfg)


def t_of(iso_s: str) -> float:
    return datetime.fromisoformat(iso_s.replace("Z", "+00:00")).timestamp() - EPOCH.timestamp()


class TwinService:
    def __init__(self):
        self.s = get_settings()
        self.policy = SafetyPolicy(self.s)
        self.lock = threading.RLock()
        self.jobs = JobManager(self.s.worker_threads)
        self.persist_errors: Dict[str, int] = {}
        self.registry = ModelRegistry(self.s.data_dir / "models")
        self.mapper = TagMapper()
        self.state, self.state_msg = "INITIALISING", "starting"
        self.mode = Mode.DEMO_SIMULATION.value
        self.mode_since = iso(0)
        self.speed = self.s.sim_speed
        self.persist_samples = True
        self.seq = 0
        self.stream: deque = deque(maxlen=900)
        self.telemetry: deque = deque(maxlen=int(48 * 3600 / 120))
        self.twin_hist: deque = deque(maxlen=int(48 * 3600 / 120))
        self.events: deque = deque(maxlen=800)
        self.recs: Dict[str, dict] = {}
        self.forecasts: Dict[str, object] = {}
        self.recon: Optional[dict] = None
        self.rom: Optional[StepResponseModel] = None
        self.dmc: Optional[DMCPredictor] = None
        self.ml_models: Dict[str, object] = {}
        self.ml_meta: Dict[str, dict] = {}
        self.replay_iter = None
        self.replay_info: dict = {}
        self.edge_queue: deque = deque(maxlen=20000)
        self.model_ok, self.model_msg = True, ""
        self.data_t = 0.0
        self.last_good_t: Optional[float] = None
        self.last_tick_wall: Optional[float] = None
        self.cfg: Optional[dict] = None
        self.config_version = 0
        self.twin: Optional[BlastFurnaceModel] = None
        self.plant: Optional[SyntheticPlant] = None
        self.setpoints: dict = {}
        self.shadow_job: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ===================================================================================
    # lifecycle
    # ===================================================================================
    def start(self, background: bool = True):
        self._seed_models()
        cfg = self._load_or_create_config()
        self._build(cfg, background=background)
        if self.s.sim_autostart and self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="bf-live-loop", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _seed_models(self):
        """Copy shipped (DEMO-SYNTHETIC) model artefacts into the runtime registry if absent."""
        try:
            if SHIPPED_MODELS.exists() and SHIPPED_MODELS.resolve() != self.registry.root.resolve():
                import shutil
                for d in SHIPPED_MODELS.iterdir():
                    if d.is_dir() and not (self.registry.root / d.name).exists():
                        shutil.copytree(d, self.registry.root / d.name)
        except Exception as e:  # pragma: no cover
            log.warning("could not seed shipped models: %s", e)

    def _load_or_create_config(self) -> dict:
        try:
            with session() as db:
                row = (db.query(PlantConfigVersion).filter_by(active=True)
                       .order_by(PlantConfigVersion.version.desc()).first())
                if row:
                    self.config_version = row.version
                    return copy.deepcopy(row.config)
                cfg = build_config(self.s.plant_preset)
                if not db.get(Plant, cfg["plant"]["id"]):
                    db.add(Plant(id=cfg["plant"]["id"], name=cfg["plant"]["name"], data_origin="SYNTHETIC"))
                    db.flush()
                db.add(PlantConfigVersion(plant_id=cfg["plant"]["id"], version=1, config=cfg, sha256=cfg_hash(cfg),
                                          author="system", comment=f"preset {self.s.plant_preset}", active=True))
                self.config_version = 1
                return cfg
        except Exception as e:  # DB unavailable -> run on defaults (reported in status)
            log.warning("config DB unavailable: %s", e)
            self.config_version = 0
            return build_config(self.s.plant_preset)

    def rebuild(self, cfg: dict, version: int):
        with self.lock:
            self.config_version = version
            self.state, self.state_msg = "INITIALISING", "rebuilding for new configuration"
        self._build(cfg, background=True)

    def _build(self, cfg: dict, background: bool):
        def work():
            try:
                with self.lock:
                    self.state, self.state_msg = "INITIALISING", "building physics twin (warm start)"
                twin = self._initial_twin(cfg)
                plant_model = twin.clone()
                tcfg = truth_config(cfg)
                for key in ("calibration", "coke", "plant"):        # in place: sub-objects share this dict
                    plant_model.cfg[key] = tcfg[key]
                plant_model._prepare_materials()
                ratio = tcfg["calibration"]["wall_h_in_mult"] / max(cfg["calibration"].get("wall_h_in_mult", 1.0), 1e-9)
                for w in plant_model.walls + [plant_model.w_sump, plant_model.w_upper, plant_model.w_bottom]:
                    w.h_in_mult = ratio
                ekf_seed = twin.clone()
                with self.lock:
                    self.cfg = cfg
                    self.twin = twin
                    self.plant = SyntheticPlant(cfg, seed=11, model=plant_model)
                    self.setpoints = copy.deepcopy(cfg["operation"])
                    self.quality = QualityEngine()
                    if plant_model.n_r == 1:
                        why = "not provided by the 1-D (single radial sector) demo plant - needs the hi-fi radial model"
                        self.quality.not_provided = {"ABP_T_CENTER": why, "ABP_T_WALL": why}
                    self.ekf = ParameterEKF(twin.cfg)
                    self.alarms = AlarmEngine(cfg)
                    self.model_ok, self.model_msg = True, ""
                    self.data_t = twin.t
                    self.telemetry.clear()
                    self.twin_hist.clear()
                    self._load_ml()
                    self._load_rom()
                    self.state, self.state_msg = "RUNNING", "ready"
                self._event("SYSTEM", f"Twin initialised: {cfg['plant']['name']} (config v{self.config_version})")
                self._compute_sensitivities(ekf_seed, background)
            except Exception as e:  # pragma: no cover
                log.exception("build failed")
                self.state, self.state_msg = "FAILED", f"initialisation failed: {e}"
        if background:
            threading.Thread(target=work, name="bf-build", daemon=True).start()
        else:
            work()

    def _compute_sensitivities(self, model: BlastFurnaceModel, background: bool):
        """Furnace-specific observer sensitivities (finite differences on a clone). Until they exist the
        EKF estimates only the HMT/Si bias states."""
        def work():
            try:
                S = compute_sensitivities(model)
                with self.lock:
                    self.ekf.set_sensitivities(S, "physics twin finite differences (4 h, last-hour mean) at start-up")
                self._event("OBSERVER", "Observer sensitivities computed from the physics twin - parameter estimation active")
            except Exception as e:  # pragma: no cover
                self._event("OBSERVER", f"Sensitivity computation failed ({e}) - bias-only estimation")
        if background:
            threading.Thread(target=work, name="bf-sens", daemon=True).start()
        else:
            work()

    def _initial_twin(self, cfg: dict) -> BlastFurnaceModel:
        preset = cfg["plant"].get("preset", "large_4000")
        p = DEMO_DIR / f"initial_state_{preset}.pkl.gz"
        meta = self._shipped_meta(preset)
        if p.exists() and _artefact_matches(meta, cfg) and meta.get("model_version") == MODEL_VERSION:
            try:
                if not meta.get("file_sha256") or file_sha256(p) != meta["file_sha256"]:
                    raise RuntimeError("snapshot SHA-256 does not match its sidecar - not deserialised")
                with gzip.open(p, "rb") as f:
                    m = pickle.load(f)  # nosec B301
                for key in NON_PHYSICS_KEYS:                 # current targets/limits/prices, same physics
                    if key in cfg:
                        m.cfg[key] = copy.deepcopy(cfg[key])
                probe = m.clone()
                probe.step()                       # validate the artefact against the current code
                if out.balances(probe)["step"]["status"] == "OK":
                    self.state_msg = "initial state loaded from shipped snapshot"
                    return m
            except Exception as e:
                log.warning("initial state not usable (%s) - warm starting", e)
        m = BlastFurnaceModel(cfg)
        m.warm_start(hours=36.0)
        m.run(6.0)
        return m

    def _shipped_meta(self, preset: str) -> dict:
        p = DEMO_DIR / f"initial_state_{preset}.json"
        try:
            return json.loads(p.read_text()) if p.exists() else {}
        except Exception:
            return {}

    # ===================================================================================
    # live loop
    # ===================================================================================
    def _loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                if self.state == "RUNNING":
                    if self.mode in (Mode.DEMO_SIMULATION.value, Mode.ADVISORY_SHADOW_MPC.value):
                        self.tick()
                    elif self.mode == Mode.HISTORICAL_REPLAY.value:
                        self.tick_replay()
                    elif self.mode == Mode.LIVE_MONITOR_ONLY.value:
                        self.tick_edge()
            except Exception as e:
                log.exception("live loop error")
                self.model_ok, self.model_msg = False, str(e)
                self._event("ALARM", f"Live loop error: {e}")
                time.sleep(2.0)
            dt = self.twin.dt if self.twin else 120.0
            interval = dt if self.mode == Mode.LIVE_MONITOR_ONLY.value else dt / max(self.speed, 0.1)
            time.sleep(max(interval - (time.time() - t0), 0.05))

    def tick(self):
        """One DEMO/SHADOW step: synthetic plant -> sensors -> quality -> twin -> observer -> alarms."""
        with self.lock:
            plant = self.plant
            plant.apply_inputs(self.setpoints)
            readings = plant.step(self.twin.dt)
            t_data = plant.model.t
            now = iso(t_data)
            assessed = self.quality.assess([r.as_dict() for r in readings], now)
            self._ingest(assessed, now, t_data)
            meas = {r["tag"]: r for r in assessed}
            op_twin = copy.deepcopy(self.setpoints)
            for tag in ("BL_FLOW", "BL_TEMP", "BL_MOIST", "BL_O2", "PCI_RATE"):
                r = meas.get(tag)
                if r and r["quality"] == "GOOD" and r["value"] is not None:
                    op_twin[TAG_INDEX[tag].model_key] = r["value"]
            self._step_twin(op_twin, assessed, now, t_data)
            for ev in plant.model.last_record.events:
                if ev["type"] in ("CAST_START", "CAST_END"):
                    self._event("CAST", f"{ev['type'].replace('_', ' ').title()} - cast {ev.get('cast_no')}, taphole {ev.get('taphole')}",
                                {k: v for k, v in ev.items() if k in ("cast_no", "taphole", "hm_t", "slag_t", "duration_min")})

    def _step_twin(self, op_twin: dict, assessed: List[dict], now: str, t_data: float):
        twin = self.twin
        for k, v in op_twin.items():
            if k in twin.op:
                twin.op[k] = copy.deepcopy(v)
        try:
            twin.step()
            self.model_ok, self.model_msg = True, ""
        except Exception as e:
            self.model_ok, self.model_msg = False, f"twin step failed: {e}"
            raise
        self.data_t = t_data
        self.last_tick_wall = time.time()
        k = out.kpis(twin)
        tg = k["top_gas_T_C"]["value"]
        if tg is not None:
            a = 1.0 - np.exp(-twin.dt / 600.0)        # same uptake/thermocouple lag as the measured TG_TEMP
            self._tg_lag = tg if getattr(self, "_tg_lag", None) is None else self._tg_lag + a * (tg - self._tg_lag)
        y_twin = {"stave_heat_kW": sum(w.Q_water_last for w in twin.walls) / 1e3, "eta_CO_pct": k["eta_CO_pct"]["value"],
                  "top_gas_T_C": getattr(self, "_tg_lag", tg),
                  # same definition as the measured DP_TOTAL (blast-main minus top pressure, incl. tuyere loss)
                  "pressure_drop_kPa": (None if k["blast_pressure_kPa_g"]["value"] is None else
                                        k["blast_pressure_kPa_g"]["value"] - float(twin.op["top_pressure_kPa_g"])),
                  "hmt_C": k["hmt_C"]["value"], "hm_si_pct": k["hm_si_pct"]["value"]}
        self.ekf.record_twin(t_data, y_twin)
        self.ekf.predict(twin.dt)
        ym, tm = self._observer_measurements(assessed)
        if ym:
            self.ekf.update(ym, tm, t_data)
        if twin.step_no % 8 == 0:
            self.ekf.apply_to_twin(twin)
        dq = self.quality.score(now)
        bal_ok = out.balances(twin)["step"]["status"] == "OK"
        before = set(self.alarms.active)
        k_al = dict(k)
        if getattr(self, "_tg_lag", None) is not None:   # alarm on the uptake-averaged value, not the charging ripple
            k_al["top_gas_T_C"] = {**k["top_gas_T_C"], "value": self._tg_lag}
        act = self.alarms.evaluate(now, k_al, dq, bal_ok, self.ekf.status(), self.model_ok,
                                   {r["tag"]: r["value"] for r in assessed})
        for code in set(self.alarms.active) - before:
            a = self.alarms.active[code]
            self._event("ALARM", f"[{a['severity']}] {a['message']}", {"code": code})
            self._persist_alarm(a)
        row = {"t_s": t_data, "t_utc": now, **{kk: k[kk]["value"] for kk in STREAM_KPIS}}
        row["hmt_corrected_C"] = None if row["hmt_C"] is None else row["hmt_C"] + float(self.ekf.theta[4])
        row["top_gas_T_uptake_C"] = getattr(self, "_tg_lag", None)
        row["si_corrected_pct"] = None if row["hm_si_pct"] is None else row["hm_si_pct"] + float(self.ekf.theta[5])
        row["casting"] = bool(twin.hearth.cast.casting)
        rec = twin.last_record
        row["el_res_max"] = max_rel_residual(rec)
        row["E_res_pct"] = float(100 * (rec.E_in - rec.E_out - rec.E_acc) / max(abs(rec.E_in) + abs(rec.E_out), 1.0))
        self.twin_hist.append(row)
        if twin.step_no % 8 == 0:
            self._forecast()
            if self.mode == Mode.ADVISORY_SHADOW_MPC.value and self.rom is not None:
                self._submit_shadow()
        if twin.step_no % 30 == 0:
            self._reconcile()
            self._persist_observer(now)
        self.seq += 1
        self.stream.append(_json_safe({"seq": self.seq, "type": "tick", "t_utc": now, "mode": self.mode,
                                       "kpis": {kk: row[kk] for kk in STREAM_KPIS},
                                       "hmt_corrected_C": row["hmt_corrected_C"], "alarms": len(act),
                                       "critical": self.alarms.critical_active(), "dq_score": dq["score"],
                                       "casting": row["casting"], "balance_ok": bal_ok}))

    def _observer_measurements(self, assessed):
        ym, tm = {}, {}
        m = {r["tag"]: r for r in assessed if r["quality"] == "GOOD" and r["value"] is not None}
        st = [m.get(t) for t in ("ST_Q_BOSH", "ST_Q_BELLY", "ST_Q_MID", "ST_Q_UPPER")]
        if all(st):
            ym["stave_heat_kW"] = sum(r["value"] for r in st)
            tm["stave_heat_kW"] = t_of(st[0]["ts_sensor"])
        if "TG_CO" in m and "TG_CO2" in m:
            ym["eta_CO_pct"] = 100 * m["TG_CO2"]["value"] / max(m["TG_CO"]["value"] + m["TG_CO2"]["value"], 1e-9)
            tm["eta_CO_pct"] = t_of(m["TG_CO"]["ts_sensor"])
        for name, tag in (("top_gas_T_C", "TG_TEMP"), ("pressure_drop_kPa", "DP_TOTAL"), ("hmt_C", "HMT_DIP"),
                          ("hm_si_pct", "HM_SI")):
            if tag in m:
                ym[name] = m[tag]["value"]
                tm[name] = t_of(m[tag]["ts_sensor"])
        return ym, tm

    def _ingest(self, assessed: List[dict], now: str, t_data: float):
        row = {"t_s": t_data, "t_utc": now}
        crit_good = True
        for r in assessed:
            row[r["tag"]] = r["value"]
            row[r["tag"] + "__q"] = r["quality"]
            td = TAG_INDEX.get(r["tag"])
            if td and td.signal_type == "LAB":
                row[r["tag"] + "__t_sample"] = t_of(r["ts_sensor"])
            if r["tag"] in ("BL_FLOW", "TG_CO", "TG_TEMP") and r["quality"] != "GOOD":
                crit_good = False
        if crit_good:
            self.last_good_t = t_data
        self.telemetry.append(row)
        if self.persist_samples and int(round(t_data / 120.0)) % 5 == 0:
            try:
                ts = datetime.fromisoformat(now.replace("Z", "+00:00"))
                with session() as db:
                    db.add(RawSample(plant_id=self.cfg["plant"]["id"], ts=ts, source=MODE_INFO[self.mode]["data_origin"],
                                     payload=_json_safe({r["tag"]: [r["value"], r["quality"]] for r in assessed})))
                    self._raw_n = getattr(self, "_raw_n", 0) + 1
                    if self._raw_n % 500 == 0:       # rolling retention by data time (RAW_SAMPLE_RETENTION_DAYS)
                        keep = timedelta(days=get_settings().raw_sample_retention_days)
                        db.query(RawSample).filter(RawSample.ts < ts - keep).delete(synchronize_session=False)
            except Exception as e:
                self._persist_failed("raw sample", e)

    # ---------------------------------------------------------------------------------
    def tick_replay(self):
        with self.lock:
            if self.replay_iter is None:
                self.set_mode_internal(Mode.DEMO_SIMULATION.value, "no replay source")
                return
            try:
                row = next(self.replay_iter)
            except StopIteration:
                self.replay_iter = None
                self._event("SYSTEM", "Historical replay completed - returning to demo simulation")
                self.set_mode_internal(Mode.DEMO_SIMULATION.value, "replay finished")
                return
            now = row["t_utc"]
            readings = []
            for td in TAGS:
                v = row.get(td.tag)
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    continue
                ts_s = now
                ts_sample = row.get(td.tag + "__t_sample")
                if td.signal_type == "LAB" and ts_sample is not None and not (isinstance(ts_sample, float) and np.isnan(ts_sample)):
                    ts_s = iso(float(ts_sample))
                readings.append({"tag": td.tag, "value": float(v), "unit": td.unit, "ts_sensor": ts_s, "ts_ingest": now,
                                 "source": "HISTORICAL_FILE"})
            assessed = self.quality.assess(readings, now)
            t_data = float(row["t_s"])
            self._ingest(assessed, now, t_data)
            op_twin = copy.deepcopy(self.setpoints)
            for r in assessed:
                td = TAG_INDEX.get(r["tag"])
                if td and td.mv and r["quality"] == "GOOD" and r["value"] is not None and td.model_key in op_twin:
                    op_twin[td.model_key] = r["value"]
            self._step_twin(op_twin, assessed, now, t_data)
            self.replay_info["rows_done"] = self.replay_info.get("rows_done", 0) + 1

    def tick_edge(self):
        with self.lock:
            if not self.edge_queue:
                return
            batch = []
            while self.edge_queue:
                batch.append(self.edge_queue.popleft())
            now = max(r["ts_ingest"] for r in batch)
            assessed = self.quality.assess(batch, now)
            t_data = t_of(now)
            self._ingest(assessed, now, t_data)
            op_twin = copy.deepcopy(self.setpoints)
            for r in assessed:
                td = TAG_INDEX.get(r["tag"])
                if td and td.mv and r["quality"] == "GOOD" and r["value"] is not None and td.model_key in op_twin:
                    op_twin[td.model_key] = r["value"]
            self._step_twin(op_twin, assessed, now, t_data)

    def ingest_edge(self, readings: List[dict]) -> dict:
        """Read-only edge gateway push (LIVE_MONITOR_ONLY). Tags must be catalogue or mapped tags."""
        accepted, rejected = 0, []
        now_wall = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for r in readings:
            tag, val = r["tag"], r.get("value")
            if tag not in TAG_INDEX:
                if tag not in self.mapper.mapping:
                    rejected.append({"tag": tag, "reason": "unmapped tag (POST /api/v1/tags/map first)"})
                    continue
                mtag, mval = self.mapper.apply(tag, val if val is not None else 0.0)
                tag, val = mtag, (mval if val is not None else None)
            self.edge_queue.append({"tag": tag, "value": val, "unit": TAG_INDEX[tag].unit, "ts_sensor": r["ts_utc"],
                                    "ts_ingest": now_wall, "source": "LIVE_EDGE"})
            accepted += 1
        return {"accepted": accepted, "rejected": rejected, "queued": len(self.edge_queue)}

    # ===================================================================================
    # status / modes / demo controls
    # ===================================================================================
    def _fresh(self) -> bool:
        if not self.twin or self.last_good_t is None or self.last_tick_wall is None:
            return False
        if self.mode == Mode.OFFLINE_SCENARIO.value:
            return (self.data_t - self.last_good_t) <= 900.0
        dt = self.twin.dt
        wall_limit = 900.0 if self.mode == Mode.LIVE_MONITOR_ONLY.value else max(60.0, 6 * dt / max(self.speed, 0.1))
        return (self.data_t - self.last_good_t) <= 900.0 and (time.time() - self.last_tick_wall) <= wall_limit

    def status(self) -> dict:
        with self.lock:
            twin = self.twin
            ready = twin is not None and self.state == "RUNNING"
            now = iso(self.data_t) if twin else None
            dq = self.quality.score(now) if ready else {"score": None, "critical_ok": False}
            bal = out.balances(twin)["step"]["status"] if ready else "NOT_COMPUTED"
            obs = self.ekf.status() if ready else {}
            fresh = self._fresh() if ready else False
            if not ready:
                health = self.state
            elif self.model_ok and bal == "OK" and dq.get("critical_ok") and fresh:
                health = "HEALTHY"
            else:
                health = "DEGRADED"
            reasons = []
            if ready:
                if not fresh:
                    reasons.append("data not fresh (no GOOD critical data within 15 min data-time, or loop stalled)")
                if bal != "OK":
                    reasons.append("conservation check failed")
                if not dq.get("critical_ok"):
                    reasons.append("critical tags not GOOD: " + ", ".join(dq.get("critical_not_good", [])))
                if not self.model_ok:
                    reasons.append(self.model_msg)
            info = MODE_INFO[self.mode]
            return {"api_version": "v1", "app_version": APP_VERSION, "model_version": MODEL_VERSION,
                    "state": self.state, "state_message": self.state_msg, "mode": self.mode,
                    "mode_since_utc": self.mode_since, "mode_description": info["description"],
                    "data_origin": info["data_origin"], "feedback": info["feedback"],
                    "write_disabled": True, "plant_writes": "DISABLED - this release contains no plant-write path",
                    "data_time_utc": now, "data_epoch_utc": EPOCH.isoformat().replace("+00:00", "Z"), "sim_speed": self.speed,
                    "last_good_data_utc": iso(self.last_good_t) if self.last_good_t is not None else None,
                    "data_fresh": fresh, "model_health": health, "health_reasons": reasons, "model_ok": self.model_ok,
                    "model_message": self.model_msg, "balance_status": bal, "data_quality_score": dq.get("score"),
                    "critical_tags_ok": dq.get("critical_ok"), "observer_updates": obs.get("updates"),
                    "active_alarms": len(self.alarms.active) if ready else 0,
                    "critical_alarm": self.alarms.critical_active() if ready else False,
                    "plant": (self.cfg or {}).get("plant"), "config_version": self.config_version,
                    "fidelity": twin.fidelity if twin else None, "rom_available": self.rom is not None,
                    "ml_models": {k: {"scope": v.get("scope"), "approval": v.get("approval_status")} for k, v in self.ml_meta.items()},
                    "deployment_tier": self.s.deployment_tier, "demo_mode": self.s.demo_mode, "seq": self.seq,
                    "replay": self.replay_info if self.mode == Mode.HISTORICAL_REPLAY.value else None,
                    "label": ("SYNTHETIC DEMONSTRATION - not connected to any physical furnace"
                              if info["data_origin"] in ("SYNTHETIC", "SYNTHETIC_OR_LIVE", "USER_SCENARIO") else info["data_origin"])}

    def require_ready(self):
        if self.twin is None or self.state != "RUNNING":
            raise DomainError("TWIN_NOT_READY", f"Twin state is {self.state}: {self.state_msg}", 503)

    def set_mode(self, target: str, principal, reason: str = ""):
        with self.lock:
            self.policy.check_mode_transition(self.mode, target, principal.role, bool(self.s.edge_ingest_token))
            if target == Mode.HISTORICAL_REPLAY.value and self.replay_iter is None:
                self._prepare_replay()
            self.set_mode_internal(target, reason, principal)

    def set_mode_internal(self, target, reason="", principal=None):
        prev = self.mode
        self.mode = target
        self.mode_since = iso(self.data_t)
        self.audit(principal.sub if principal else "system", principal.role if principal else "SYSTEM", "MODE_CHANGE",
                   target, {"from": prev, "to": target, "reason": reason})
        self._event("MODE", f"Mode {prev} -> {target}" + (f" ({reason})" if reason else ""))

    def _prepare_replay(self, df: Optional[pd.DataFrame] = None):
        if df is None:
            p = DEMO_DIR / "history.csv.gz"
            if not p.exists():
                raise DomainError("NO_REPLAY_DATA", "No historical file available (upload one via /api/v1/data/upload).", 409)
            df = pd.read_csv(p)
            src = "shipped synthetic history (data/demo/history.csv.gz)"
        else:
            src = "uploaded file"
        df = df.sort_values("t_s")
        self.replay_iter = iter(df.to_dict("records"))
        self.replay_info = {"source": src, "rows": int(len(df)), "rows_done": 0,
                            "start_utc": str(df["t_utc"].iloc[0]), "end_utc": str(df["t_utc"].iloc[-1])}

    def load_replay_frame(self, df: pd.DataFrame):
        with self.lock:
            self._prepare_replay(df)

    def set_speed(self, speed: float):
        if not (0.1 <= speed <= 240):
            raise DomainError("OUT_OF_RANGE", "speed must be within [0.1, 240]", 422)
        self.speed = float(speed)

    def set_setpoints(self, changes: Dict[str, object], principal) -> dict:
        """DEMO ONLY: change the setpoints of the SYNTHETIC plant (never a real plant)."""
        if self.mode not in (Mode.DEMO_SIMULATION.value, Mode.ADVISORY_SHADOW_MPC.value):
            raise DomainError("NOT_IN_DEMO", "Synthetic-plant setpoints can only be changed in DEMO/SHADOW modes.", 409)
        with self.lock:
            self.require_ready()
            lim = self.cfg["equipment_limits"]
            new = copy.deepcopy(self.setpoints)
            for k, v in changes.items():
                if k not in SETPOINT_KEYS:
                    raise DomainError("UNKNOWN_SETPOINT", f"'{k}' is not a manipulable setpoint", 422)
                if k in lim and isinstance(v, (int, float)) and not isinstance(v, bool):
                    lo, hi = lim[k]
                    if not (lo <= float(v) <= hi):
                        raise DomainError("OUTSIDE_ENVELOPE", f"{k}={v} outside equipment envelope [{lo}, {hi}]", 422)
                if k in ("coke_batch_t", "ore_batch_t"):
                    ore = float(changes.get("ore_batch_t", new["ore_batch_t"]))
                    coke = float(changes.get("coke_batch_t", new["coke_batch_t"]))
                    oc_lo, oc_hi = lim.get("ore_coke_ratio", [3.2, 6.0])
                    if coke <= 0 or not (oc_lo <= ore / coke <= oc_hi):
                        raise DomainError("OUTSIDE_ENVELOPE", f"ore/coke ratio outside [{oc_lo}, {oc_hi}]", 422)
                if k == "burden_mix":
                    if (not isinstance(v, dict) or abs(sum(float(x) for x in v.values()) - 1.0) > 1e-3
                            or any(float(x) < 0 for x in v.values())):
                        raise DomainError("INVALID_MIX", "burden_mix fractions must be >= 0 and sum to 1", 422)
                    unknown = set(v) - set(self.cfg["materials"])
                    if unknown:
                        raise DomainError("INVALID_MIX", f"unknown materials {sorted(unknown)}", 422)
                if k == "n_tuyeres_closed" and not (0 <= int(v) <= self.cfg["geometry"]["n_tuyeres"] // 3):
                    raise DomainError("OUTSIDE_ENVELOPE", "n_tuyeres_closed out of range", 422)
                new[k] = v
            self.setpoints = new
        self.audit(principal.sub, principal.role, "DEMO_SETPOINT_CHANGE", "synthetic-plant",
                   {"changes": changes, "effect": "synthetic plant only - no real plant"})
        self._event("SETPOINT", "Synthetic plant setpoints changed: " + ", ".join(f"{k}={v}" for k, v in changes.items()))
        return {"setpoints": self.setpoints, "applies_to": "SYNTHETIC PLANT (demo) - never a physical furnace"}

    def inject_fault(self, spec: dict, principal) -> dict:
        if self.mode not in (Mode.DEMO_SIMULATION.value, Mode.ADVISORY_SHADOW_MPC.value):
            raise DomainError("NOT_IN_DEMO", "Sensor faults can only be injected into the synthetic plant.", 409)
        if spec["tag"] not in TAG_INDEX:
            raise DomainError("UNKNOWN_TAG", f"unknown tag {spec['tag']}", 422)
        with self.lock:
            self.require_ready()
            h = self.plant.model.t / 3600.0
            f = FaultSpec(spec["kind"], spec["tag"], float(spec.get("magnitude", 0.0)), h, h + float(spec.get("duration_h", 2.0)))
            self.plant.faults.append(f)
            active = [vars(x) for x in self.plant.faults if x.end_h > h]
        self.audit(principal.sub, principal.role, "DEMO_FAULT_INJECTED", spec["tag"], spec)
        self._event("FAULT", f"Injected {f.kind} on {f.tag} for {spec.get('duration_h', 2.0)} h (synthetic sensor layer)")
        return {"fault": vars(f), "active_faults": active}

    def clear_faults(self):
        with self.lock:
            if self.plant:
                self.plant.faults = []
                self.plant.stuck = {}

    # ===================================================================================
    # derived products for the API
    # ===================================================================================
    def kpis(self) -> dict:
        with self.lock:
            self.require_ready()
            k = out.kpis(self.twin)
            if k["hmt_C"]["value"] is not None:
                k["hmt_corrected_C"] = out.q(k["hmt_C"]["value"] + float(self.ekf.theta[4]), "degC", "HYBRID",
                                            note="physics + observer bias state (EKF)")
            if k["hm_si_pct"]["value"] is not None:
                k["si_corrected_pct"] = out.q(k["hm_si_pct"]["value"] + float(self.ekf.theta[5]), "wt%", "HYBRID",
                                             note="physics + observer bias state (EKF)")
            if getattr(self, "_tg_lag", None) is not None:
                k["top_gas_T_uptake_C"] = out.q(self._tg_lag, "degC", "MODEL",
                                                note="uptake-equivalent: model top gas T with the 10-min thermocouple lag "
                                                     "(the instantaneous value swings with every charge)")
            return k

    def _forecast(self):
        twin = self.twin
        k = out.kpis(twin)
        res = {"t_utc": iso(self.data_t), "items": {}}
        df = self._history_frame(hours=14)
        for key, hf in self.ml_models.items():
            target, hz = key.split("@")
            meta = self.ml_meta.get(key, {})
            phys_key = "hmt_C" if target == "hmt" else "hm_si_pct"
            bias = float(self.ekf.theta[4] if target == "hmt" else self.ekf.theta[5])
            phys_now = k[phys_key]["value"]
            item = {"target": target, "horizon_h": float(hz), "physics": phys_now,
                    "physics_plus_observer": None if phys_now is None else phys_now + bias,
                    "status": "PHYSICS_ONLY", "scope": meta.get("scope"), "model_id": meta.get("model_id"),
                    "approval": meta.get("approval_status")}
            try:
                if len(df) >= 30:
                    X, phys = live_row(df, target)
                    p = hf.predict(X, phys)
                    item.update({"prediction": float(p["prediction"][0]), "lower95": float(p["lower95"][0]),
                                 "upper95": float(p["upper95"][0]), "correction": float(p["correction"][0]),
                                 "ood": bool(p["ood"][0]), "mahalanobis": float(p["mahalanobis"][0]),
                                 "promoted": bool(meta.get("beats_baselines_on_test")),
                                 "status": "OOD_FALLBACK_TO_PHYSICS" if p["ood"][0] else
                                           ("HYBRID" if meta.get("beats_baselines_on_test") else "ML_SHADOW_NOT_PROMOTED")})
                else:
                    item["reason"] = "insufficient history for features"
            except Exception as e:
                item["error"] = str(e)[:200]
            res["items"][key] = item
        self.forecasts = _json_safe(res)
        if self.persist_samples and res["items"]:
            try:
                with session() as db:
                    db.add(ForecastRun(plant_id=self.cfg["plant"]["id"],
                                       ts=datetime.fromisoformat(res["t_utc"].replace("Z", "+00:00")), result=self.forecasts))
            except Exception as e:
                self._persist_failed("forecast run", e)

    def history_frame(self, hours: float = 14) -> pd.DataFrame:
        with self.lock:
            return self._history_frame(hours)

    def _history_frame(self, hours: float = 14) -> pd.DataFrame:
        n = int(hours * 3600 / (self.twin.dt if self.twin else 120.0))
        tel = list(self.telemetry)[-n:]
        th = list(self.twin_hist)[-n:]
        if not tel or not th:
            return pd.DataFrame()
        a = pd.DataFrame(tel)
        b = pd.DataFrame(th).drop(columns=["t_utc"], errors="ignore")
        b = b.rename(columns={c: "phys_" + c for c in b.columns if c != "t_s"}).rename(columns={"phys_hm_si_pct": "phys_si_pct"})
        return pd.merge_asof(a.sort_values("t_s"), b.sort_values("t_s"), on="t_s")

    def _load_ml(self):
        self.ml_models, self.ml_meta = {}, {}
        for meta in self.registry.list():
            key = f"{meta['target']}@{float(meta['horizon_h'])}"
            try:
                obj = self.registry.load(meta["model_id"])
                prev = self.ml_meta.get(key)
                if prev is None or "load_error" in prev or str(meta.get("created_utc")) >= str(prev.get("created_utc")):
                    self.ml_models[key] = obj
                    self.ml_meta[key] = meta
            except Exception as e:
                self.ml_meta.setdefault(key, {**meta, "load_error": str(e)[:200], "scope": "UNAVAILABLE (artefact incompatible)"})

    def reload_ml(self):
        with self.lock:
            self._load_ml()

    def _load_rom(self):
        preset = self.cfg["plant"].get("preset", "large_4000")
        p = DEMO_DIR / f"rom_{preset}.json"
        meta = self._shipped_meta(preset)
        self.rom, self.dmc = None, None
        if p.exists() and _artefact_matches(meta, self.cfg):
            self.set_rom(json.loads(p.read_text()), source="shipped identification (physics-twin step tests)")

    def set_rom(self, d: dict, source: str):
        S = np.array([[[d["step_response"][c][mv][k] for mv in d["mvs"]] for c in d["cvs"]] for k in range(d["N"] + 1)])
        self.rom = StepResponseModel(d["Ts_min"] * 60, d["N"], d["mvs"], d["cvs"], S, d["op_point"], d["identified_utc"],
                                     source=source, ident_seconds=d.get("ident_seconds", 0.0))
        self.dmc = DMCPredictor(self.rom)

    def _reconcile(self):
        tel = self.telemetry[-1] if self.telemetry else None
        need = ["BL_FLOW", "BL_O2", "TG_CO", "TG_CO2", "TG_H2", "PCI_RATE"]
        if not tel or any(tel.get(t) is None or tel.get(t + "__q") != "GOOD" for t in need):
            return
        twin = self.twin
        sp = out.specific(twin, 4.0)
        prod_t_h = sp["production_t_d"] / 24.0
        coke_C = sp["coke_kg_tHM"] * prod_t_h * twin.cfg["coke"]["C_pct"] / 100 / 12.011
        hm_C = prod_t_h * 1000 * (out.kpis(twin)["hm_c_pct"]["value"] or 4.7) / 100 / 12.011
        try:
            r = bf_reconciliation({t: tel[t] for t in need}, {"coke_C_kmol_h": coke_C, "hm_C_kmol_h": hm_C,
                                                               "pci_C_frac": twin.cfg["pci"]["C_pct"] / 100})
            r["t_utc"] = tel["t_utc"]
            r["inputs_note"] = "air/oxygen/PCI/top-gas from tags; coke C and hot-metal C from the twin's 4-h windows"
            self.recon = _json_safe(r)
        except Exception as e:
            self.recon = {"error": str(e)}

    # ===================================================================================
    # MPC (advisory only)
    # ===================================================================================
    def _submit_shadow(self):
        if self.shadow_job:
            j = self.jobs.get(self.shadow_job)
            if j and j["status"] in ("PENDING", "RUNNING"):
                return
        try:
            job = self.jobs.submit("mpc_shadow", {"mode": self.mode},
                                   lambda cb: self.recommend(user="system:shadow", method="linear", persist=True),
                                   "system:shadow")
        except DomainError:          # queue full: skip this shadow cycle, try again next time
            return
        self.shadow_job = job["id"]

    def mpc_context(self) -> dict:
        now = iso(self.data_t)
        dq = self.quality.score(now)
        bal_ok = out.balances(self.twin)["step"]["status"] == "OK"
        obs = self.ekf.status()
        return {"mode": self.mode, "dq_score": dq["score"], "critical_ok": dq["critical_ok"], "fresh": self._fresh(),
                "balance_ok": bal_ok, "model_ok": self.model_ok,
                "observer_ok": (obs.get("mean_normalised_innovation") or 0.0) < 6.0,
                "critical_alarm": self.alarms.critical_active()}

    def recommend(self, user: str, method: str = "linear", persist: bool = True, targets: Optional[dict] = None,
                  availability: Optional[dict] = None, econ_weight: float = 0.0, override_hard: Optional[dict] = None,
                  context_override: Optional[dict] = None, price_mult: Optional[dict] = None,
                  verify: bool = True) -> dict:
        with self.lock:
            self.require_ready()
            if self.rom is None:
                raise DomainError("ROM_UNAVAILABLE", "Reduced-order model not identified yet (POST /api/v1/mpc/identify).", 409)
            twin = self.twin
            ctx = self.mpc_context()
            ctx.update(context_override or {})
            gate = trust_gate(ctx)
            y_now = read_cvs(twin)
            if y_now.get("hmt_C") is not None:
                y_now["hmt_C"] += float(self.ekf.theta[4])
            if y_now.get("hm_si_pct") is not None:
                y_now["hm_si_pct"] += float(self.ekf.theta[5])
            u_now = {mv: float(twin.op[mv]) for mv in self.rom.mvs}
            cfgc = copy.deepcopy(self.cfg)
            for c in cfgc["constraints"]:
                if override_hard and c["name"] in override_hard:
                    c.update(override_hard[c["name"]])
            cs = build_constraints(cfgc, twin.op, availability)
            # HMT is the primary thermal setpoint (window midpoint). Si is ZONE-controlled: inside its window
            # the target is the current value (no push), outside it the nearest window edge - Si and HMT respond
            # almost collinearly to every lever, so two conflicting setpoints would force large opposing moves.
            si_lo, si_hi = self.cfg["targets"]["si_pct"]
            si_now = y_now.get("hm_si_pct")
            si_t = float(np.clip(si_now, si_lo, si_hi)) if si_now is not None else float(np.mean([si_lo, si_hi]))
            tgt = {"hmt_C": float(np.mean(self.cfg["targets"]["hmt_C"])), "hm_si_pct": si_t,
                   "fuel_rate_kg_tHM": (y_now.get("fuel_rate_kg_tHM") or 500.0)}     # hold; cost is the economic term
            tgt.update(targets or {})
            target_basis = {"hmt_C": "user" if targets and "hmt_C" in targets else "midpoint of target window",
                            "hm_si_pct": "user" if targets and "hm_si_pct" in targets else
                                         ("zone: inside window -> hold current" if si_now is not None and si_lo <= si_now <= si_hi
                                          else "zone: nearest window edge"),
                            "fuel_rate_kg_tHM": "user" if targets and "fuel_rate_kg_tHM" in targets else "hold current"}
            econ = self._econ_gradients(price_mult)
            twin_clone = twin.clone()
            rom, dmc = self.rom, self.dmc
            bias = (float(self.ekf.theta[4]), float(self.ekf.theta[5]))
            sd_hmt = float(np.sqrt(self.ekf.P[4, 4]))
            data_utc, cfg_v, mode = iso(self.data_t), self.config_version, self.mode
        base_payload = {"targets": tgt, "target_basis": target_basis, "trust_gate": gate, "y_now": y_now, "u_now": u_now, "method": method,
                        "data_time_utc": data_utc, "config_version": cfg_v, "model_version": MODEL_VERSION,
                        "observer_bias": {"hmt_C": bias[0], "si_pct": bias[1]},
                        "rom": {"identified_utc": rom.identified_utc, "Ts_min": rom.Ts_s / 60, "N": rom.N, "source": rom.source}}
        if not gate["passed"]:
            rec = build_recommendation("ADVICE_SUPPRESSED", mode, base_payload,
                                       [f"trust check failed: {f}" for f in gate["failed"]])
            return self._store_rec(rec, persist, user)
        prob = MPCProblem(targets=tgt, econ_weight=econ_weight, econ_INR_per_unit_h=econ)
        if method == "nmpc_physics":
            mvs = [mv for mv in ("blast_temp_C", "pci_t_h", "o2_enrichment_pct") if cs.mvs[mv].available]
            r = solve_nmpc_physics(twin_clone, mvs, {"hmt_C": tgt["hmt_C"] - bias[0], "hm_si_pct": tgt["hm_si_pct"] - bias[1]},
                                   cs, budget_s=self.s.nmpc_time_budget_s)
            if r["status"] == "TIMEOUT":
                rec = build_recommendation("ADVICE_SUPPRESSED", mode, {**base_payload, "nmpc": r}, [r["message"]])
                return self._store_rec(rec, persist, user)
            half = int(round(r["horizon_h"] * 3600 / 2 / rom.Ts_s))
            moves = {mv: [r["u_blocks"][mv][0] - u_now[mv], r["u_blocks"][mv][1] - r["u_blocks"][mv][0]] for mv in mvs}
            blocks = [0, half]
            ver = verify_on_physics(twin_clone, moves, blocks, rom.Ts_s, cs)
            status = "PROPOSED" if ver["passed"] else "ADVICE_SUPPRESSED"
            rec = build_recommendation(status, mode, {**base_payload, "nmpc": _json_safe(r), "moves": moves, "blocks": blocks,
                                                      "verification": ver, "Ts_min": rom.Ts_s / 60,
                                                      "explanation": explain(moves, rom.gain(), rom.mvs, rom.cvs, mv_scales(u_now))},
                                       ver["issues"])
            return self._store_rec(rec, persist, user)
        res = solve_linear_mpc(rom, dmc, y_now, u_now, cs, prob)
        payload = {**base_payload, "solver": {"name": res.solver, "status": res.status, "seconds": res.solve_time_s,
                                              "message": res.message},
                   "objective": res.objective, "active_constraints": res.active, "constraints": cs.as_dict(),
                   "blocks": prob.blocks, "Ts_min": rom.Ts_s / 60, "weights": prob.weights, "move_weights": prob.move_weights,
                   "econ_weight": econ_weight}
        if res.status not in ("OPTIMAL", "OPTIMAL_INACCURATE"):
            st = res.status if res.status in ("NO_FEASIBLE_SOLUTION", "NO_ACTIVE_MV") else "ADVICE_SUPPRESSED"
            extra = {"predicted_free": _traj(res.y_free, rom)} if res.y_free.size else {}
            rec = build_recommendation(st, mode, {**payload, **extra}, [res.message or res.status])
            return self._store_rec(rec, persist, user)
        payload.update({"moves": res.moves, "u_trajectory": res.u_traj, "predicted_free": _traj(res.y_free, rom),
                        "predicted_optimized": _traj(res.y_opt, rom),
                        "explanation": explain(res.moves, rom.gain(), rom.mvs, rom.cvs, mv_scales(u_now)),
                        "uncertainty": {"hmt_sd_now_C": sd_hmt, "growth_C_per_h": 2.5,
                                        "note": "band = +/-1.96 x sqrt(sd_now^2 + (2.5 degC/h x t)^2) - illustrative growth"}})
        if not verify:
            rec = build_recommendation("PROPOSED", mode, {**payload, "verification": {"passed": None, "skipped": True}}, [])
            rec["status"] = "STUDY_ONLY"
            return self._store_rec(rec, persist, user)
        ver = verify_on_physics(twin_clone, res.moves, prob.blocks, rom.Ts_s, cs, rom_pred=res.y_opt, cvs=rom.cvs)
        payload["verification"] = ver
        status = "PROPOSED" if ver["passed"] else "ADVICE_SUPPRESSED"
        rec = build_recommendation(status, mode, payload, ver["issues"])
        return self._store_rec(rec, persist, user)

    def _econ_gradients(self, price_mult: Optional[dict] = None) -> dict:
        cfg = self.cfg
        pm = price_mult or {}
        sp = out.specific(self.twin, 4.0)
        ch_h = sp["charges_per_h"]
        V = self.twin.op["blast_volume_Nm3_min"]
        return {"pci_t_h": cfg["pci"]["price_INR_t"] * pm.get("pci", 1.0),
                "coke_batch_t": cfg["coke"]["price_INR_t"] * pm.get("coke", 1.0) * ch_h,
                "o2_enrichment_pct": cfg["economics"]["oxygen_INR_Nm3"] * pm.get("oxygen", 1.0) * V * 60 / 100 / 0.79,
                "blast_temp_C": 1.4 * V * 60 / 1e6 / 0.8 * cfg["economics"]["bfg_value_INR_GJ"],
                "blast_volume_Nm3_min": cfg["economics"]["electricity_INR_kWh"] * cfg["economics"]["blower_kWh_per_Nm3"] * 60,
                "blast_moisture_g_Nm3": 0.0}

    def _store_rec(self, rec: dict, persist: bool, user: str) -> dict:
        rec = _json_safe(rec)
        rec["requested_by"] = user
        with self.lock:
            self.recs[rec["id"]] = rec
            while len(self.recs) > 300:
                self.recs.pop(next(iter(self.recs)))
        if persist:
            try:
                with session() as db:
                    db.add(MPCRecommendation(id=rec["id"], plant_id=self.cfg["plant"]["id"], status=rec["status"],
                                             mode=rec["mode"], payload=rec))
            except Exception as e:
                self._persist_failed("MPC recommendation", e)
        self._event("MPC", f"Recommendation {rec['id'][:8]}: {rec['status']}")
        return rec

    def get_rec(self, rid: str) -> dict:
        rec = self.recs.get(rid)
        if rec is None:
            try:
                with session() as db:
                    row = db.get(MPCRecommendation, rid)
                    if row:
                        rec = dict(row.payload)
                        rec["status"] = row.status
            except Exception:
                rec = None
        if rec is None:
            raise DomainError("NOT_FOUND", "Recommendation not found", 404)
        if rec["status"] == "PROPOSED" and datetime.fromisoformat(rec["expires_utc"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
            rec["status"] = "EXPIRED"
        return rec

    def acknowledge_rec(self, rid: str, principal, decision: str, comment: str) -> dict:
        rec = self.get_rec(rid)
        try:
            acknowledge(rec, principal.sub, principal.role, decision, comment)
        except ValueError as e:
            raise DomainError("INVALID_STATE", str(e), 409)
        self.recs[rid] = rec
        try:
            with session() as db:
                row = db.get(MPCRecommendation, rid)
                if row:
                    row.status = rec["status"]
                    row.payload = _json_safe(rec)
                db.add(RecommendationAcknowledgment(recommendation_id=rid, user=principal.sub, role=principal.role,
                                                    decision=decision, comment=comment))
        except Exception as e:
            self._persist_failed("recommendation acknowledgement", e)
        self.audit(principal.sub, principal.role, "RECOMMENDATION_" + decision, rid,
                   {"comment": comment, "effect": "audit state only - no plant write"})
        self._event("MPC", f"Recommendation {rid[:8]} {decision.lower()} by {principal.sub} (audit only)")
        return rec

    # ===================================================================================
    # utilities
    # ===================================================================================
    def _persist_failed(self, what: str, e: Exception):
        """Persistence is best effort - the twin keeps running through a database outage - but never silent."""
        n = self.persist_errors.get(what, 0) + 1
        self.persist_errors[what] = n
        if n == 1 or n % 100 == 0:
            log.warning("could not persist %s (%d failure(s) so far): %s", what, n, e)

    def _event(self, kind: str, msg: str, detail: Optional[dict] = None):
        ev = {"t_utc": iso(self.data_t), "type": kind, "message": msg, "detail": _json_safe(detail or {})}
        with self.lock:
            self.events.append(ev)
            self.seq += 1
            self.stream.append({"seq": self.seq, "type": "event", **ev})

    def audit(self, actor: str, role: str, action: str, target: str = "", detail: Optional[dict] = None, rid: str = ""):
        try:
            with session() as db:
                db.add(AuditEvent(actor=actor, role=role, action=action, target=str(target)[:200],
                                  detail=_json_safe(detail or {}), request_id=rid))
        except Exception:
            log.warning("audit write failed: %s %s", action, target)

    def _persist_alarm(self, a: dict):
        if not self.persist_samples:
            return
        try:
            with session() as db:
                db.add(Alarm(id=a["id"], plant_id=self.cfg["plant"]["id"], code=a["code"], severity=a["severity"],
                             ts=datetime.fromisoformat(a["first_seen"].replace("Z", "+00:00")), message=a["message"],
                             detail=_json_safe({"evidence": a["evidence"], "possible_causes": a["possible_causes"],
                                                "classification": a["classification"]})))
        except Exception as e:
            self._persist_failed("alarm", e)

    def _persist_observer(self, now: str):
        if not self.persist_samples:
            return
        try:
            with session() as db:
                db.add(ObserverSnapshot(plant_id=self.cfg["plant"]["id"], ts=datetime.fromisoformat(now.replace("Z", "+00:00")),
                                        state=_json_safe({"theta": self.ekf.theta.tolist(), "P_diag": np.diag(self.ekf.P).tolist(),
                                                          "params": ["wall_heat_mult", "coke_reactivity_mult", "reduction_mult",
                                                                     "permeability_mult", "hmt_bias_C", "si_bias_pct"]})))
        except Exception as e:
            self._persist_failed("observer snapshot", e)

    def geometry(self) -> dict:
        with self.lock:
            self.require_ready()
            g = geometry_payload(self.cfg["geometry"], self.twin.grid)
            g["walls"] = [{"name": w["name"], "frac_from": w["frac_from"], "frac_to": w["frac_to"], "layers": w["layers"],
                           "cooling": w["cooling"]["type"]} for w in self.cfg["walls"]["shaft"]]
            g["hearth_walls"] = {k: {"layers": v["layers"], "cooling": v["cooling"]["type"]}
                                 for k, v in self.cfg["walls"].items() if k != "shaft"}
            for k in ("throat_diameter_m", "hearth_diameter_m", "belly_diameter_m", "n_tuyeres", "n_tapholes",
                      "stockline_m", "sump_depth_m", "taphole_to_tuyere_m", "bosh_height_m", "belly_height_m",
                      "stack_height_m", "throat_height_m", "tuyere_diameter_m"):
                g[k] = self.cfg["geometry"][k]
            g["volumes"] = self.twin.vol
            return _json_safe(g)


def _traj(Y: np.ndarray, rom: StepResponseModel) -> dict:
    t = [round(k * rom.Ts_s / 3600.0, 3) for k in range(Y.shape[0])]
    return {"t_h": t, **{c: [None if not np.isfinite(v) else float(v) for v in Y[:, i]] for i, c in enumerate(rom.cvs)}}


_SERVICE: Optional[TwinService] = None


def get_service() -> TwinService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = TwinService()
    return _SERVICE
