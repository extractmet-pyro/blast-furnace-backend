"""Illustrative plant presets (NOT certified for any facility).

All values are typical-industry demonstration assumptions for an anonymised plant
"Industry-X"; every number must be replaced by site data before calibration.
Four capacity classes are provided: mini (~350 m3), medium (~1450 m3), large (~4000 m3),
mega (~5100 m3 working volume).
"""
from __future__ import annotations

import copy
from typing import Dict

DEMO_LABEL = "ILLUSTRATIVE DEMO VALUE - not certified for any facility"

MATERIALS = {
    "sinter": {"name": "Sinter (fluxed, B2~1.9)", "kind": "sinter", "moisture_pct": 1.0, "size_mm": 18.0,
               "bulk_density_t_m3": 1.85, "voidage": 0.42, "RI": 1.00, "softening_C": 1180.0, "melting_C": 1440.0,
               "price_INR_t": 7200.0, "availability_t_day": 12000.0, "chem_uncertainty_pct": 1.5,
               "chem": {"Fe_total": 56.5, "FeO": 9.0, "SiO2": 5.4, "Al2O3": 2.6, "CaO": 10.2, "MgO": 1.9, "MnO": 0.35,
                        "P": 0.07, "S": 0.02, "K2O": 0.08, "Na2O": 0.05, "ZnO": 0.01}},
    "pellet": {"name": "Pellets (acid)", "kind": "pellet", "moisture_pct": 1.5, "size_mm": 12.0, "bulk_density_t_m3": 2.15,
               "voidage": 0.38, "RI": 1.10, "softening_C": 1100.0, "melting_C": 1400.0, "price_INR_t": 9800.0,
               "availability_t_day": 5000.0, "chem_uncertainty_pct": 1.0,
               "chem": {"Fe_total": 63.5, "FeO": 0.6, "SiO2": 3.6, "Al2O3": 2.3, "CaO": 1.4, "MgO": 0.9, "MnO": 0.1,
                        "P": 0.05, "S": 0.01, "K2O": 0.05, "Na2O": 0.03}},
    "lump": {"name": "Calibrated lump ore", "kind": "lump", "moisture_pct": 3.5, "size_mm": 18.0, "bulk_density_t_m3": 2.30,
             "voidage": 0.40, "RI": 0.85, "softening_C": 1080.0, "melting_C": 1380.0, "price_INR_t": 6200.0,
             "availability_t_day": 4000.0, "chem_uncertainty_pct": 2.0,
             "chem": {"Fe_total": 62.8, "FeO": 0.3, "SiO2": 3.3, "Al2O3": 2.6, "CaO": 0.1, "MgO": 0.1, "MnO": 0.15,
                      "P": 0.07, "S": 0.01, "LOI_H2O": 3.0}},
    "hbi": {"name": "HBI (hot briquetted iron)", "kind": "hbi", "moisture_pct": 0.5, "size_mm": 40.0, "bulk_density_t_m3": 2.70,
            "voidage": 0.45, "RI": 1.0, "softening_C": 1250.0, "melting_C": 1450.0, "price_INR_t": 28000.0,
            "availability_t_day": 1500.0, "chem_uncertainty_pct": 1.0,
            "chem": {"Fe_total": 90.5, "Fe_met": 84.0, "FeO": 8.36, "C": 1.2, "SiO2": 2.4, "Al2O3": 1.2, "CaO": 0.9,
                     "MgO": 0.4, "MnO": 0.1, "P": 0.04, "S": 0.005}},
    "dri": {"name": "Cold DRI", "kind": "dri", "moisture_pct": 0.5, "size_mm": 10.0, "bulk_density_t_m3": 1.75,
            "voidage": 0.42, "RI": 1.0, "softening_C": 1250.0, "melting_C": 1450.0, "price_INR_t": 24000.0,
            "availability_t_day": 800.0, "chem_uncertainty_pct": 1.5,
            "chem": {"Fe_total": 89.0, "Fe_met": 82.0, "FeO": 9.0, "C": 2.5, "SiO2": 3.0, "Al2O3": 1.6, "CaO": 1.0,
                     "MgO": 0.5, "MnO": 0.1, "P": 0.05, "S": 0.01}},
    "limestone": {"name": "Limestone", "kind": "limestone", "moisture_pct": 1.0, "size_mm": 30.0, "bulk_density_t_m3": 1.55,
                  "voidage": 0.45, "RI": 1.0, "softening_C": 1250.0, "melting_C": 1450.0, "price_INR_t": 1800.0,
                  "availability_t_day": 1000.0, "chem_uncertainty_pct": 1.0,
                  "chem": {"CaO": 52.0, "MgO": 1.5, "SiO2": 2.0, "Al2O3": 0.5, "CO2": 42.0}},
    "dolomite": {"name": "Dolomite", "kind": "dolomite", "moisture_pct": 1.0, "size_mm": 30.0, "bulk_density_t_m3": 1.60,
                 "voidage": 0.45, "RI": 1.0, "softening_C": 1250.0, "melting_C": 1450.0, "price_INR_t": 2200.0,
                 "availability_t_day": 1000.0, "chem_uncertainty_pct": 1.0,
                 "chem": {"CaO": 30.5, "MgO": 20.5, "SiO2": 2.5, "Al2O3": 0.6, "CO2": 45.5}},
    "quartzite": {"name": "Quartzite", "kind": "quartzite", "moisture_pct": 1.0, "size_mm": 25.0, "bulk_density_t_m3": 1.60,
                  "voidage": 0.44, "RI": 1.0, "softening_C": 1300.0, "melting_C": 1500.0, "price_INR_t": 1500.0,
                  "availability_t_day": 500.0, "chem_uncertainty_pct": 1.0,
                  "chem": {"SiO2": 96.0, "Al2O3": 1.5, "Fe_total": 0.7, "CaO": 0.3, "MgO": 0.2}},
}

COKE = {"name": "BF coke (metallurgical)", "moisture_pct": 4.5, "ash_pct": 11.5, "C_pct": 86.0, "H_pct": 0.35,
        "N_pct": 1.1, "S_pct": 0.55, "O_pct": 0.5, "VM_pct": 0.9, "CSR": 64.0, "CRI": 24.0, "size_mm": 50.0,
        "bulk_density_t_m3": 0.48, "voidage": 0.48, "price_INR_t": 34000.0, "HHV_MJ_kg": 29.5,
        "ash_chem": {"SiO2": 50.0, "Al2O3": 28.0, "Fe2O3": 9.0, "CaO": 5.0, "MgO": 1.5, "P2O5": 1.2, "K2O": 2.0, "Na2O": 0.8}}

PCI = {"name": "PCI coal (low-volatile blend)", "moisture_pct": 1.0, "C_pct": 78.5, "H_pct": 4.4, "O_pct": 5.6,
       "N_pct": 1.7, "S_pct": 0.6, "ash_pct": 9.2, "VM_pct": 22.0, "HHV_MJ_kg": 31.2, "price_INR_t": 16000.0,
       "carrier_N2_Nm3_per_kg": 0.03, "inject_temp_C": 60.0,
       "ash_chem": {"SiO2": 55.0, "Al2O3": 28.0, "Fe2O3": 7.0, "CaO": 5.0, "MgO": 1.5, "P2O5": 0.5, "K2O": 2.0, "Na2O": 1.0}}


def _walls(scale: float) -> list:
    """Wall-zone template by fractional height of the working column (0 = tuyere, 1 = stockline)."""
    return [
        {"name": "bosh", "frac_from": 0.0, "frac_to": 0.17, "h_in_W_m2K": 220.0,
         "layers": [{"material": "skull_accretion", "thickness_m": 0.05}, {"material": "sic_si3n4", "thickness_m": 0.15},
                    {"material": "copper_stave", "thickness_m": 0.12}],
         "cooling": {"type": "stave_water", "water_flow_m3_h_per_m2": 1.6, "water_inlet_C": 38.0, "pipe_d_m": 0.05,
                     "pipes_per_m2": 3.0, "ambient_C": 35.0}},
        {"name": "belly_lower_stack", "frac_from": 0.17, "frac_to": 0.40, "h_in_W_m2K": 180.0,
         "layers": [{"material": "skull_accretion", "thickness_m": 0.04}, {"material": "sic_si3n4", "thickness_m": 0.20},
                    {"material": "copper_stave", "thickness_m": 0.12}],
         "cooling": {"type": "stave_water", "water_flow_m3_h_per_m2": 1.4, "water_inlet_C": 38.0, "pipe_d_m": 0.05,
                     "pipes_per_m2": 3.0, "ambient_C": 35.0}},
        {"name": "mid_stack", "frac_from": 0.40, "frac_to": 0.75, "h_in_W_m2K": 110.0,
         "layers": [{"material": "high_alumina", "thickness_m": 0.23}, {"material": "cast_iron_stave", "thickness_m": 0.14}],
         "cooling": {"type": "stave_water", "water_flow_m3_h_per_m2": 1.0, "water_inlet_C": 38.0, "pipe_d_m": 0.05,
                     "pipes_per_m2": 2.5, "ambient_C": 35.0}},
        {"name": "upper_stack_throat", "frac_from": 0.75, "frac_to": 1.0, "h_in_W_m2K": 60.0,
         "layers": [{"material": "chamotte", "thickness_m": 0.23}, {"material": "cast_iron_stave", "thickness_m": 0.14}],
         "cooling": {"type": "stave_water", "water_flow_m3_h_per_m2": 0.8, "water_inlet_C": 38.0, "pipe_d_m": 0.05,
                     "pipes_per_m2": 2.0, "ambient_C": 35.0}},
    ]


def _hearth_walls(scale: float) -> dict:
    t = 1.0 if scale >= 0.8 else (0.8 if scale >= 0.5 else 0.6)
    return {
        "sump_wall": {"h_in_W_m2K": 450.0, "layers": [{"material": "ceramic_cup", "thickness_m": 0.25 * t},
                                                      {"material": "carbon_micropore", "thickness_m": 1.15 * t},
                                                      {"material": "ramming_mass", "thickness_m": 0.08},
                                                      {"material": "cast_iron_stave", "thickness_m": 0.12}],
                      "cooling": {"type": "stave_water", "water_flow_m3_h_per_m2": 1.2, "water_inlet_C": 36.0,
                                  "pipe_d_m": 0.05, "pipes_per_m2": 3.0, "ambient_C": 35.0},
                      "thermocouple_depth_from_cold_face_m": 0.35},
        "upper_hearth_wall": {"h_in_W_m2K": 300.0, "layers": [{"material": "carbon_micropore", "thickness_m": 0.9 * t},
                                                              {"material": "ramming_mass", "thickness_m": 0.08},
                                                              {"material": "cast_iron_stave", "thickness_m": 0.12}],
                              "cooling": {"type": "stave_water", "water_flow_m3_h_per_m2": 1.2, "water_inlet_C": 36.0,
                                          "pipe_d_m": 0.05, "pipes_per_m2": 3.0, "ambient_C": 35.0},
                              "thermocouple_depth_from_cold_face_m": 0.30},
        "hearth_bottom": {"h_in_W_m2K": 400.0, "layers": [{"material": "ceramic_cup", "thickness_m": 0.4 * t},
                                                          {"material": "carbon_micropore", "thickness_m": 1.2 * t},
                                                          {"material": "semi_graphite", "thickness_m": 0.4 * t},
                                                          {"material": "ramming_mass", "thickness_m": 0.1}],
                          "cooling": {"type": "bottom_water", "water_flow_m3_h_per_m2": 0.6, "water_inlet_C": 36.0,
                                      "pipe_d_m": 0.06, "pipes_per_m2": 1.5, "ambient_C": 35.0},
                          "thermocouple_depth_from_cold_face_m": 0.40},
    }


PRESETS: Dict[str, dict] = {
    "mini_350": {
        "label": "Mini BF ~350 m3 (illustrative)",
        "geometry": {"hearth_diameter_m": 5.0, "sump_depth_m": 1.2, "taphole_to_tuyere_m": 1.9, "bosh_height_m": 2.4,
                     "belly_diameter_m": 5.9, "belly_height_m": 1.2, "stack_height_m": 11.5, "throat_diameter_m": 4.0,
                     "throat_height_m": 1.4, "stockline_m": 1.2, "n_tuyeres": 14, "tuyere_diameter_m": 0.10,
                     "n_tapholes": 1, "taphole_diameter_m": 0.040, "deadman_porosity": 0.40, "chute_length_m": 2.0},
        "operation": {"blast_volume_Nm3_min": 950.0, "blast_temp_C": 1080.0, "blast_moisture_g_Nm3": 20.0,
                      "o2_enrichment_pct": 2.5, "pci_t_h": 4.0, "ng_Nm3_h": 0.0, "h2_Nm3_h": 0.0, "top_pressure_kPa_g": 90.0,
                      "coke_batch_t": 4.2, "ore_batch_t": 13.5},
        "tuyere_heat_kW": 160.0, "scale": 0.25,
        "thermal_window": {"hmt_C": [1480.0, 1515.0], "si_pct": [0.45, 0.75]},   # consistent with this furnace's Si-HMT relation
    },
    "medium_1500": {
        "label": "Medium BF ~1450 m3 (illustrative)",
        "geometry": {"hearth_diameter_m": 9.6, "sump_depth_m": 1.8, "taphole_to_tuyere_m": 2.8, "bosh_height_m": 3.2,
                     "belly_diameter_m": 10.8, "belly_height_m": 1.8, "stack_height_m": 15.0, "throat_diameter_m": 7.4,
                     "throat_height_m": 1.8, "stockline_m": 1.5, "n_tuyeres": 26, "tuyere_diameter_m": 0.12,
                     "n_tapholes": 2, "taphole_diameter_m": 0.060, "deadman_porosity": 0.40, "chute_length_m": 3.2},
        "operation": {"blast_volume_Nm3_min": 3000.0, "blast_temp_C": 1150.0, "blast_moisture_g_Nm3": 18.0,
                      "o2_enrichment_pct": 3.5, "pci_t_h": 24.0, "ng_Nm3_h": 0.0, "h2_Nm3_h": 0.0, "top_pressure_kPa_g": 170.0,
                      "coke_batch_t": 10.1, "ore_batch_t": 43.0},
        "tuyere_heat_kW": 250.0, "scale": 0.55,
        "thermal_window": {"hmt_C": [1480.0, 1515.0], "si_pct": [0.35, 0.60]},   # consistent with this furnace's Si-HMT relation
    },
    "large_4000": {
        "label": "Large BF ~4000 m3 (illustrative)",
        "geometry": {"hearth_diameter_m": 14.2, "sump_depth_m": 2.5, "taphole_to_tuyere_m": 3.2, "bosh_height_m": 4.2,
                     "belly_diameter_m": 15.6, "belly_height_m": 2.5, "stack_height_m": 19.5, "throat_diameter_m": 11.0,
                     "throat_height_m": 2.2, "stockline_m": 1.6, "n_tuyeres": 36, "tuyere_diameter_m": 0.14,
                     "n_tapholes": 4, "taphole_diameter_m": 0.080, "deadman_porosity": 0.40, "chute_length_m": 4.2},
        "operation": {"blast_volume_Nm3_min": 6600.0, "blast_temp_C": 1180.0, "blast_moisture_g_Nm3": 22.0,
                      "o2_enrichment_pct": 4.5, "pci_t_h": 72.0, "ng_Nm3_h": 0.0, "h2_Nm3_h": 0.0, "top_pressure_kPa_g": 230.0,
                      "coke_batch_t": 22.9, "ore_batch_t": 108.0},
        "tuyere_heat_kW": 320.0, "scale": 1.0,
        "thermal_window": {"hmt_C": [1500.0, 1535.0], "si_pct": [0.25, 0.50]},   # consistent with this furnace's Si-HMT relation
    },
    "mega_5000": {
        "label": "Mega BF ~5100 m3 (illustrative)",
        "geometry": {"hearth_diameter_m": 15.5, "sump_depth_m": 3.0, "taphole_to_tuyere_m": 3.6, "bosh_height_m": 4.4,
                     "belly_diameter_m": 17.0, "belly_height_m": 2.8, "stack_height_m": 21.5, "throat_diameter_m": 11.6,
                     "throat_height_m": 2.4, "stockline_m": 1.8, "n_tuyeres": 40, "tuyere_diameter_m": 0.14,
                     "n_tapholes": 4, "taphole_diameter_m": 0.090, "deadman_porosity": 0.40, "chute_length_m": 4.6},
        "operation": {"blast_volume_Nm3_min": 8600.0, "blast_temp_C": 1200.0, "blast_moisture_g_Nm3": 16.0,
                      "o2_enrichment_pct": 5.0, "pci_t_h": 92.0, "ng_Nm3_h": 0.0, "h2_Nm3_h": 0.0, "top_pressure_kPa_g": 260.0,
                      "coke_batch_t": 28.2, "ore_batch_t": 140.0},
        "tuyere_heat_kW": 330.0, "scale": 1.3,
        "thermal_window": {"hmt_C": [1485.0, 1520.0], "si_pct": [0.30, 0.55]},   # consistent with this furnace's Si-HMT relation
    },
}


def charging_matrix(scale: float) -> list:
    """Illustrative bell-less-top charging matrix (angle measured from vertical)."""
    return [
        {"angle_deg": 12.0, "coke_rot": 2.0, "ore_rot": 0.0},   # centre coke
        {"angle_deg": 28.0, "coke_rot": 1.0, "ore_rot": 1.0},
        {"angle_deg": 33.0, "coke_rot": 2.0, "ore_rot": 2.0},
        {"angle_deg": 37.0, "coke_rot": 2.0, "ore_rot": 3.0},
        {"angle_deg": 40.5, "coke_rot": 2.0, "ore_rot": 3.0},
        {"angle_deg": 43.5, "coke_rot": 2.0, "ore_rot": 3.0},
    ]


def build_config(preset: str = "large_4000") -> dict:
    if preset not in PRESETS:
        raise KeyError(f"Unknown preset '{preset}'. Available: {sorted(PRESETS)}")
    p = PRESETS[preset]
    s = p["scale"]
    tw = p["thermal_window"]
    op = copy.deepcopy(p["operation"])
    op.update({
        "burden_mix": {"sinter": 0.70, "pellet": 0.20, "lump": 0.10, "hbi": 0.0, "dri": 0.0},
        "flux_kg_per_charge": {"limestone": 0.0, "dolomite": 0.0, "quartzite": 0.0},
        "charging_matrix": charging_matrix(s),
        "charge_temperature_C": 30.0,
        "pci_inject_temp_C": 60.0,
        "o2_purity_pct": 99.5,
        "n_tuyeres_closed": 0,
        "blast_on": True,
    })
    cfg = {
        "schema_version": "1.0",
        "plant": {"id": f"industry-x-{preset}", "name": f"Industry-X BF ({p['label']})", "preset": preset,
                  "data_origin": "SYNTHETIC", "certified": False, "note": DEMO_LABEL},
        "geometry": copy.deepcopy(p["geometry"]),
        "walls": {"shaft": _walls(s), **_hearth_walls(s)},
        "materials": copy.deepcopy(MATERIALS),
        "coke": copy.deepcopy(COKE),
        "pci": copy.deepcopy(PCI),
        "natural_gas": {"CH4_pct": 100.0, "price_INR_Nm3": 28.0, "inject_temp_C": 25.0},
        "hydrogen": {"price_INR_Nm3": 22.0, "inject_temp_C": 25.0},
        "operation": op,
        "tapping": {"open_level_above_taphole_m": 0.9, "max_cast_min": 150.0, "min_gap_min": 12.0,
                    "taphole_erosion_mm_per_h": 7.0, "discharge_coeff": 0.62, "lab_delay_min": 25.0,
                    "drainage_window_m": 1.0},
        "dust": {"ore_dust_frac": 0.006, "coke_dust_frac": 0.003},
        "targets": {"hmt_C": list(tw["hmt_C"]), "si_pct": list(tw["si_pct"]), "s_pct_max": 0.040, "B2": [1.10, 1.25],
                    "MgO_Al2O3": [0.5, 0.85], "slag_Al2O3_pct_max": 18.0},
        "equipment_limits": {
            "blast_volume_Nm3_min": [round(op["blast_volume_Nm3_min"] * 0.80), round(op["blast_volume_Nm3_min"] * 1.10)],
            "blast_temp_C": [950.0, 1250.0], "blast_moisture_g_Nm3": [8.0, 45.0], "o2_enrichment_pct": [0.0, 9.0],
            "pci_t_h": [0.0, round(op["pci_t_h"] * 1.35, 1) if op["pci_t_h"] > 0 else 5.0],
            "top_pressure_kPa_g": [round(op["top_pressure_kPa_g"] * 0.6), round(op["top_pressure_kPa_g"] * 1.12)],
            "ore_coke_ratio": [3.2, 6.0], "pressure_drop_kPa_max": 210.0 * min(max(s, 0.5), 1.1),
            "tuyere_velocity_m_s": [180.0, 290.0],
        },
        "rate_limits_per_15min": {"blast_volume_Nm3_min": round(op["blast_volume_Nm3_min"] * 0.03),
                                  "blast_temp_C": 20.0, "blast_moisture_g_Nm3": 3.0, "o2_enrichment_pct": 0.5,
                                  "pci_t_h": max(round(op["pci_t_h"] * 0.06, 1), 0.5), "ore_coke_ratio": 0.08},
        "model": {"fidelity": "fast", "n_z": 14, "n_r": 1, "dt_s": 120.0, "n_sub": 2, "grid_cluster": 0.80,
                  "hifi": {"n_z": 28, "n_r": 3, "dt_s": 90.0, "n_sub": 1, "grid_cluster": 0.70}},
        "calibration": {
            "kinetic_multipliers": {}, "heat_transfer_mult": 1.0, "wall_h_in_mult": 1.0, "permeability_mult": 3.3,
            "pci_burnout_eta_max": 0.92, "pci_burnout_k": 2.2, "sio_vol_k": 0.50, "sio_abs_k": 0.55,
            "si_desi_k": 2.0e-4, "s_gas_absorb_frac": 0.95, "alkali_volatile_frac": 0.12, "p_to_metal_frac": 0.95,
            "carburisation_approach": 0.95, "tuyere_heat_kW": p["tuyere_heat_kW"], "descent_wall_frac": 0.55,
            "hearth_dm_U_W_m2K": 25.0, "slag_metal_U_W_m2K": 250.0, "drip_ntu_gas": 0.25, "drip_ntu_gas_ore": 6.0, "drip_ntu_liq": 0.22, "reduction_diffusion_beta": 1.0, "melt_tau_s": 1800.0, "drip_height_ref_m": 3.0, "melt_exit_superheat_K": 0.0, "slag_feo_residual_pct": 0.45,
            "liquid_superheat_K": 0.0, "active_coke_frac": 0.30, "dm_T_soft_K": 1430.0, "dm_T_melt_K": 1700.0,
        },
        "economics": {"currency": "INR", "oxygen_INR_Nm3": 6.5, "electricity_INR_kWh": 7.5, "blower_kWh_per_Nm3": 0.032,
                      "stove_efficiency": 0.80, "bfg_value_INR_GJ": 320.0, "slag_credit_INR_t": 800.0,
                      "other_variable_INR_tHM": 950.0, "fixed_cost_INR_day": 9.5e6 * s, "stove_fuel": "BFG"},
        "carbon": {"boundary": "BF_GATE_TO_GATE", "grid_EF_kgCO2_kWh": 0.71, "oxygen_EF_kgCO2_Nm3": 0.42,
                   "coke_upstream_EF_kgCO2_t": 480.0, "sinter_upstream_EF_kgCO2_t": 210.0,
                   "pellet_upstream_EF_kgCO2_t": 95.0, "hbi_upstream_EF_kgCO2_t": 650.0, "bfg_credit_method": "NO_CREDIT"},
        "constraints": [
            {"name": "hmt_C", "unit": "degC", "low_alarm": tw["hmt_C"][0] - 20.0, "high_alarm": tw["hmt_C"][1] + 15.0,
             "low_hard": tw["hmt_C"][0] - 50.0, "high_hard": tw["hmt_C"][1] + 40.0, "soft": True},
            {"name": "si_pct", "unit": "wt%", "low_alarm": round(max(tw["si_pct"][0] - 0.10, 0.12), 2), "high_alarm": round(tw["si_pct"][1] + 0.15, 2),
             "low_hard": round(max(tw["si_pct"][0] - 0.20, 0.05), 2), "high_hard": round(tw["si_pct"][1] + 0.55, 2), "soft": True},
            {"name": "pressure_drop_kPa", "unit": "kPa", "low_alarm": None, "high_alarm": 190.0 * min(max(s, 0.5), 1.1), "low_hard": None,
             "high_hard": 210.0 * min(max(s, 0.5), 1.1), "soft": False},
            {"name": "raft_C", "unit": "degC", "low_alarm": 1950.0, "high_alarm": 2350.0, "low_hard": 1900.0, "high_hard": 2400.0, "soft": False},
            {"name": "top_gas_temp_C", "unit": "degC", "low_alarm": 90.0, "high_alarm": 280.0, "low_hard": 80.0, "high_hard": 350.0, "soft": True},
        ],
    }
    return cfg


def list_presets() -> list:
    from .zones import volumes
    out = []
    for k, p in PRESETS.items():
        v = volumes(p["geometry"])
        out.append({"id": k, "label": p["label"], "working_volume_m3": round(v["working_volume_m3"], 1),
                    "inner_volume_m3": round(v["inner_volume_m3"], 1), "n_tuyeres": p["geometry"]["n_tuyeres"],
                    "n_tapholes": p["geometry"]["n_tapholes"], "hearth_diameter_m": p["geometry"]["hearth_diameter_m"],
                    "nominal_blast_Nm3_min": p["operation"]["blast_volume_Nm3_min"], "note": DEMO_LABEL})
    return out
