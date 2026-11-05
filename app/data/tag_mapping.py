"""Plant tag catalogue (data dictionary) and site tag mapping.

Every tag carries source, unit, basis, signal type, timestamp convention (UTC storage), expected
frequency, stale timeout, physical range, max rate of change and quality defaults. Tags that are
not installed at a site are reported as UNAVAILABLE - never as zero.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class TagDef:
    tag: str
    description: str
    family: str
    unit: str
    basis: str = ""
    signal_type: str = "ANALOG"          # ANALOG | LAB | EVENT | CALC | SETPOINT
    expected_period_s: float = 120.0
    stale_timeout_s: float = 900.0
    lo: Optional[float] = None
    hi: Optional[float] = None
    roc_max_per_min: Optional[float] = None
    noise_sd: float = 0.0
    lab_delay_min: float = 0.0
    installed: bool = True
    source_id: str = "SYNTHETIC"
    calibration_interval_days: int = 90
    model_key: str = ""                  # internal model variable this tag measures (if any)
    mv: bool = False

    def as_dict(self):
        return asdict(self)


def _t(*a, **k):
    return TagDef(*a, **k)


TAGS: List[TagDef] = [
    # hot blast
    _t("BL_FLOW", "Blast volume (dry, incl. O2)", "Hot blast", "Nm3/min", "dry, 0 degC/101.325 kPa", lo=0, hi=12000, roc_max_per_min=400, noise_sd=25, model_key="blast_volume_Nm3_min", mv=True),
    _t("BL_TEMP", "Hot blast temperature", "Hot blast", "degC", lo=600, hi=1350, roc_max_per_min=40, noise_sd=3, model_key="blast_temp_C", mv=True),
    _t("BL_PRESS", "Hot blast pressure (bustle main)", "Hot blast", "kPa(g)", "gauge", lo=0, hi=600, roc_max_per_min=60, noise_sd=1.2, model_key="blast_pressure_kPa_g"),
    _t("BL_MOIST", "Blast humidity", "Hot blast", "g/Nm3", "per Nm3 dry blast", lo=0, hi=80, roc_max_per_min=5, noise_sd=0.4, model_key="blast_moisture_g_Nm3", mv=True),
    _t("BL_O2", "Oxygen enrichment", "Hot blast", "%", "O2 vol% above 20.95 (dry blast)", lo=0, hi=15, roc_max_per_min=1, noise_sd=0.04, model_key="o2_enrichment_pct", mv=True),
    # injection
    _t("PCI_RATE", "Pulverised coal injection rate", "Fuel injection", "t/h", lo=0, hi=200, roc_max_per_min=8, noise_sd=0.5, model_key="pci_t_h", mv=True),
    _t("NG_FLOW", "Natural gas injection", "Fuel injection", "Nm3/h", installed=False, model_key="ng_Nm3_h"),
    _t("H2_FLOW", "Hydrogen injection", "Fuel injection", "Nm3/h", installed=False, model_key="h2_Nm3_h"),
    # top gas
    _t("TG_CO", "Top gas CO (dry)", "Top gas", "vol%", "dry", lo=10, hi=40, roc_max_per_min=3, noise_sd=0.12, model_key="top_CO_pct"),
    _t("TG_CO2", "Top gas CO2 (dry)", "Top gas", "vol%", "dry", lo=8, hi=35, roc_max_per_min=3, noise_sd=0.12, model_key="top_CO2_pct"),
    _t("TG_H2", "Top gas H2 (dry)", "Top gas", "vol%", "dry", lo=0, hi=15, roc_max_per_min=2, noise_sd=0.08, model_key="top_H2_pct"),
    _t("TG_N2", "Top gas N2 (dry, by difference)", "Top gas", "vol%", "dry", signal_type="CALC", lo=30, hi=65, model_key="top_N2_pct"),
    _t("TG_TEMP", "Top gas temperature (uptake average)", "Top gas", "degC", lo=40, hi=500, roc_max_per_min=30, noise_sd=4, model_key="top_gas_T_C"),
    _t("TG_PRESS", "Top pressure", "Top gas", "kPa(g)", "gauge", lo=0, hi=400, roc_max_per_min=40, noise_sd=1, model_key="top_pressure_kPa_g", mv=True),
    _t("TG_DUST", "Top gas dust load", "Environmental/utility", "g/Nm3", installed=False),
    # shaft / permeability
    _t("DP_TOTAL", "Overall pressure drop (blast - top)", "Shaft gas/pressure", "kPa", signal_type="CALC", lo=0, hi=300, model_key="pressure_drop_kPa"),
    _t("SH_P_LOWER", "Shaft pressure tap - lower stack", "Shaft gas/pressure", "kPa(g)", "gauge", lo=0, hi=500, noise_sd=1.0),
    _t("SH_P_MID", "Shaft pressure tap - mid stack", "Shaft gas/pressure", "kPa(g)", "gauge", lo=0, hi=500, noise_sd=1.0),
    _t("SH_P_UPPER", "Shaft pressure tap - upper stack", "Shaft gas/pressure", "kPa(g)", "gauge", lo=0, hi=500, noise_sd=1.0),
    _t("ABP_T_CENTER", "Above-burden probe - centre", "Shaft gas/pressure", "degC", lo=40, hi=900, noise_sd=8),
    _t("ABP_T_WALL", "Above-burden probe - wall", "Shaft gas/pressure", "degC", lo=40, hi=900, noise_sd=8),
    # tuyere / raceway
    _t("TUY_CLOSED", "Tuyeres closed / blanked", "Tuyere/raceway", "count", signal_type="EVENT", lo=0, hi=50),
    _t("RAFT_CALC", "RAFT (calculated by L2 - not a measurement)", "Tuyere/raceway", "degC", signal_type="CALC", lo=1700, hi=2600, model_key="raft_C"),
    _t("TUY_Q", "Tuyere cooling heat load (total)", "Thermal/cooling", "kW", lo=0, hi=60000, noise_sd=150),
    # thermal / cooling
    _t("ST_Q_BOSH", "Stave heat load - bosh", "Thermal/cooling", "kW", lo=0, hi=60000, noise_sd=60),
    _t("ST_Q_BELLY", "Stave heat load - belly / lower stack", "Thermal/cooling", "kW", lo=0, hi=60000, noise_sd=60),
    _t("ST_Q_MID", "Stave heat load - mid stack", "Thermal/cooling", "kW", lo=0, hi=60000, noise_sd=50),
    _t("ST_Q_UPPER", "Stave heat load - upper stack / throat", "Thermal/cooling", "kW", lo=0, hi=60000, noise_sd=40),
    _t("HTC_SUMP", "Hearth wall thermocouple (sump, 0.35 m from cold face)", "Thermal/cooling", "degC", lo=0, hi=1200, noise_sd=1.5),
    _t("HTC_BOTTOM", "Hearth bottom thermocouple", "Thermal/cooling", "degC", lo=0, hi=1200, noise_sd=1.5),
    # cast house
    _t("CAST_ACTIVE", "Cast in progress", "Cast-house/tapping", "-", signal_type="EVENT"),
    _t("HMT_DIP", "Hot metal temperature (dip, during cast)", "Cast-house/tapping", "degC", signal_type="LAB", lo=1350, hi=1650, noise_sd=5, expected_period_s=1800, stale_timeout_s=10800, model_key="hmt_C"),
    _t("HM_SI", "Hot metal Si (lab)", "Cast-house/tapping", "wt%", signal_type="LAB", lo=0, hi=2.0, noise_sd=0.02, lab_delay_min=25, expected_period_s=5400, stale_timeout_s=14400, model_key="hm_si_pct"),
    _t("HM_S", "Hot metal S (lab)", "Cast-house/tapping", "wt%", signal_type="LAB", lo=0, hi=0.2, noise_sd=0.002, lab_delay_min=25, expected_period_s=5400, stale_timeout_s=14400, model_key="hm_s_pct"),
    _t("HM_C", "Hot metal C (lab)", "Cast-house/tapping", "wt%", signal_type="LAB", lo=3, hi=6, noise_sd=0.05, lab_delay_min=25, expected_period_s=5400, stale_timeout_s=14400, model_key="hm_c_pct"),
    _t("HM_MN", "Hot metal Mn (lab)", "Cast-house/tapping", "wt%", signal_type="LAB", lo=0, hi=2, noise_sd=0.01, lab_delay_min=25, expected_period_s=5400, stale_timeout_s=14400, model_key="hm_mn_pct"),
    _t("HM_P", "Hot metal P (lab)", "Cast-house/tapping", "wt%", signal_type="LAB", lo=0, hi=0.5, noise_sd=0.005, lab_delay_min=25, expected_period_s=5400, stale_timeout_s=14400, model_key="hm_p_pct"),
    _t("SLAG_B2", "Slag basicity CaO/SiO2 (lab)", "Cast-house/tapping", "-", signal_type="LAB", lo=0.6, hi=2.0, noise_sd=0.01, lab_delay_min=30, expected_period_s=5400, stale_timeout_s=14400, model_key="slag_B2"),
    _t("SLAG_AL2O3", "Slag Al2O3 (lab)", "Cast-house/tapping", "wt%", signal_type="LAB", lo=0, hi=30, noise_sd=0.15, lab_delay_min=30, expected_period_s=5400, stale_timeout_s=14400, model_key="slag_Al2O3_pct"),
    _t("SLAG_FEO", "Slag FeO (lab)", "Cast-house/tapping", "wt%", signal_type="LAB", lo=0, hi=10, noise_sd=0.05, lab_delay_min=30, expected_period_s=5400, stale_timeout_s=14400, model_key="slag_FeO_pct"),
    _t("HM_CAST_T", "Hot metal tapped per cast (torpedo weighing)", "Cast-house/tapping", "t", signal_type="EVENT"),
    # charging
    _t("STOCKLINE", "Stockline (radar), depth below zero level", "Charging/burden", "m", lo=0, hi=6, noise_sd=0.03),
    _t("CHARGES_H", "Charges per hour", "Charging/burden", "1/h", signal_type="CALC"),
    _t("COKE_BATCH", "Coke batch weight (wet)", "Charging/burden", "t", signal_type="SETPOINT", model_key="coke_batch_t", mv=True),
    _t("ORE_BATCH", "Ore batch weight (wet)", "Charging/burden", "t", signal_type="SETPOINT", model_key="ore_batch_t", mv=True),
    # equipment events
    _t("STOVE_CHANGE", "Stove change-over", "Equipment events", "-", signal_type="EVENT"),
    _t("O2_TRIP", "Oxygen supply interruption", "Equipment events", "-", signal_type="EVENT"),
]

TAG_INDEX: Dict[str, TagDef] = {t.tag: t for t in TAGS}


class TagMapper:
    """Maps site historian tag names to catalogue tags with unit conversion (validated)."""

    def __init__(self):
        self.mapping: Dict[str, dict] = {}

    def set(self, site_tag: str, catalogue_tag: str, site_unit: str, scale: float = 1.0, offset: float = 0.0):
        if catalogue_tag not in TAG_INDEX:
            raise KeyError(f"Unknown catalogue tag '{catalogue_tag}'")
        self.mapping[site_tag] = {"tag": catalogue_tag, "unit": site_unit, "scale": scale, "offset": offset}

    def apply(self, site_tag: str, value: float):
        m = self.mapping.get(site_tag)
        if not m:
            return None, None
        return m["tag"], value * m["scale"] + m["offset"]

    def as_list(self):
        return [{"site_tag": k, **v} for k, v in self.mapping.items()]
