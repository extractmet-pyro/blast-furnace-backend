"""Parameter EKF (bias tracking, chi-square gating, out-of-sequence lab handling) and WLS reconciliation."""
import numpy as np

from app.estimation.observer import MEAS, ParameterEKF
from app.estimation.reconciliation import bf_reconciliation, reconcile


def _ekf(model):
    e = ParameterEKF(model.cfg)
    for k in range(60):
        e.record_twin(k * 120.0, {"stave_heat_kW": 17000.0, "eta_CO_pct": 48.0, "top_gas_T_C": 180.0,
                                  "pressure_drop_kPa": 150.0, "hmt_C": 1500.0, "hm_si_pct": 0.40})
    return e


def test_hmt_bias_state_converges(model):
    e = _ekf(model)
    for k in range(40):
        e.predict(120.0)
        e.update({"hmt_C": 1512.0}, {"hmt_C": k * 120.0}, k * 120.0)
    i = 4
    assert 8.0 < e.theta[i] < 13.0, e.theta[i]
    assert np.sqrt(e.P[i, i]) < 6.0


def test_single_outlier_is_gated(model):
    e = _ekf(model)
    for k in range(10):
        e.predict(120.0)
        e.update({"hmt_C": 1501.0, "eta_CO_pct": 48.1}, {"hmt_C": k * 120.0, "eta_CO_pct": k * 120.0}, k * 120.0)
    before = e.theta.copy()
    n_rej = e.n_rejected
    e.update({"hmt_C": 1700.0, "eta_CO_pct": 48.0}, {"hmt_C": 1200.0, "eta_CO_pct": 1200.0}, 1200.0)
    assert e.n_rejected == n_rej + 1
    assert abs(e.theta[4] - before[4]) < 1.0            # 200 degC outlier did not move the bias


def test_out_of_sequence_lab_uses_sampling_time(model):
    e = ParameterEKF(model.cfg)
    for k in range(60):   # twin Si changes at t = 3600 s
        e.record_twin(k * 120.0, {"hm_si_pct": 0.30 if k * 120.0 < 3600 else 0.60})
    ref_early = e.twin_at(1800.0)
    assert abs(ref_early["hm_si_pct"] - 0.30) < 1e-12   # lab sampled at 1800 s compared with twin at 1800 s
    e.update({"hm_si_pct": 0.31}, {"hm_si_pct": 1800.0}, 6000.0)
    assert abs(e.theta[5] - 0.0) < 0.02


def test_reconciliation_closes_balance_and_flags_gross_error():
    tags = {"BL_FLOW": 6600.0, "BL_O2": 4.5, "TG_CO": 23.0, "TG_CO2": 22.0, "TG_H2": 4.0, "PCI_RATE": 70.0}
    r = bf_reconciliation(tags, {"coke_C_kmol_h": 9000.0, "hm_C_kmol_h": 1500.0, "pci_C_frac": 0.78})
    after = r["constraint_residual_after"]
    assert all(abs(v) < 1e-6 for v in after.values())
    # deliberately inconsistent measurement -> gross error suspected
    meas = {"a": 100.0, "b": 60.0, "c": 20.0}
    comp = {"a": {"X": 1.0}, "b": {"X": 1.0, "_out": True}, "c": {"X": 1.0, "_out": True}}
    g = reconcile(meas, {"a": 1.0, "b": 1.0, "c": 1.0}, comp)
    assert g["gross_error_suspected"] and g["suspect_streams"]
