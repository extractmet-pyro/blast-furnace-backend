# Data dictionary

Generated from `app/data/tag_mapping.py` (`python scripts/gen_data_dictionary.py`). The same catalogue is served by `GET /api/v1/tags` with the current site mapping and installation status.

Conventions
- Timestamps are UTC (ISO 8601 with `Z`). Lab results carry their **sampling** time as well as the receipt time; the observer uses a lab value at its sampling time once it has been received.
- Every value carries a quality of GOOD, SUSPECT, BAD, MISSING, UNAVAILABLE (not installed or not provided by the source) or NO_EVENT (event tags before their first event). Values are never replaced by zero.
- Units are the catalogue units below. Site units are converted on ingest using the mapping (automatic for known pairs such as kPa(g) and bar(g); otherwise scale and offset).
- `model_key` links a tag to the twin variable it is compared with; `mv` marks manipulated variables that drive the twin.
- Critical tags (bold) must be GOOD for the advice trust gate to open, and count double in the data-quality score.

| Tag | Description | Family | Unit | Signal | Period s | Range | Max rate /min | Lab delay min | Model key |
|---|---|---|---|---|---|---|---|---|---|
| **BL_FLOW** | Blast volume (dry, incl. O2) | Hot blast | Nm3/min | ANALOG | 120.0 | 0 - 12000 | 400 | - | blast_volume_Nm3_min (MV) |
| **BL_TEMP** | Hot blast temperature | Hot blast | degC | ANALOG | 120.0 | 600 - 1350 | 40 | - | blast_temp_C (MV) |
| **BL_PRESS** | Hot blast pressure (bustle main) | Hot blast | kPa(g) | ANALOG | 120.0 | 0 - 600 | 60 | - | blast_pressure_kPa_g |
| BL_MOIST | Blast humidity | Hot blast | g/Nm3 | ANALOG | 120.0 | 0 - 80 | 5 | - | blast_moisture_g_Nm3 (MV) |
| **BL_O2** | Oxygen enrichment | Hot blast | % | ANALOG | 120.0 | 0 - 15 | 1 | - | o2_enrichment_pct (MV) |
| **PCI_RATE** | Pulverised coal injection rate | Fuel injection | t/h | ANALOG | 120.0 | 0 - 200 | 8 | - | pci_t_h (MV) |
| NG_FLOW | Natural gas injection | Fuel injection | Nm3/h | ANALOG | 120.0 | - | - | - | ng_Nm3_h |
| H2_FLOW | Hydrogen injection | Fuel injection | Nm3/h | ANALOG | 120.0 | - | - | - | h2_Nm3_h |
| **TG_CO** | Top gas CO (dry) | Top gas | vol% | ANALOG | 120.0 | 10 - 40 | 3 | - | top_CO_pct |
| **TG_CO2** | Top gas CO2 (dry) | Top gas | vol% | ANALOG | 120.0 | 8 - 35 | 3 | - | top_CO2_pct |
| TG_H2 | Top gas H2 (dry) | Top gas | vol% | ANALOG | 120.0 | 0 - 15 | 2 | - | top_H2_pct |
| TG_N2 | Top gas N2 (dry, by difference) | Top gas | vol% | CALC | 120.0 | 30 - 65 | - | - | top_N2_pct |
| **TG_TEMP** | Top gas temperature (uptake average) | Top gas | degC | ANALOG | 120.0 | 40 - 500 | 30 | - | top_gas_T_C |
| **TG_PRESS** | Top pressure | Top gas | kPa(g) | ANALOG | 120.0 | 0 - 400 | 40 | - | top_pressure_kPa_g (MV) |
| TG_DUST | Top gas dust load | Environmental/utility | g/Nm3 | ANALOG | 120.0 | - | - | - | - |
| DP_TOTAL | Overall pressure drop (blast - top) | Shaft gas/pressure | kPa | CALC | 120.0 | 0 - 300 | - | - | pressure_drop_kPa |
| SH_P_LOWER | Shaft pressure tap - lower stack | Shaft gas/pressure | kPa(g) | ANALOG | 120.0 | 0 - 500 | - | - | - |
| SH_P_MID | Shaft pressure tap - mid stack | Shaft gas/pressure | kPa(g) | ANALOG | 120.0 | 0 - 500 | - | - | - |
| SH_P_UPPER | Shaft pressure tap - upper stack | Shaft gas/pressure | kPa(g) | ANALOG | 120.0 | 0 - 500 | - | - | - |
| ABP_T_CENTER | Above-burden probe - centre | Shaft gas/pressure | degC | ANALOG | 120.0 | 40 - 900 | - | - | - |
| ABP_T_WALL | Above-burden probe - wall | Shaft gas/pressure | degC | ANALOG | 120.0 | 40 - 900 | - | - | - |
| TUY_CLOSED | Tuyeres closed / blanked | Tuyere/raceway | count | EVENT | 120.0 | 0 - 50 | - | - | - |
| RAFT_CALC | RAFT (calculated by L2 - not a measurement) | Tuyere/raceway | degC | CALC | 120.0 | 1700 - 2600 | - | - | raft_C |
| TUY_Q | Tuyere cooling heat load (total) | Thermal/cooling | kW | ANALOG | 120.0 | 0 - 60000 | - | - | - |
| ST_Q_BOSH | Stave heat load - bosh | Thermal/cooling | kW | ANALOG | 120.0 | 0 - 60000 | - | - | - |
| ST_Q_BELLY | Stave heat load - belly / lower stack | Thermal/cooling | kW | ANALOG | 120.0 | 0 - 60000 | - | - | - |
| ST_Q_MID | Stave heat load - mid stack | Thermal/cooling | kW | ANALOG | 120.0 | 0 - 60000 | - | - | - |
| ST_Q_UPPER | Stave heat load - upper stack / throat | Thermal/cooling | kW | ANALOG | 120.0 | 0 - 60000 | - | - | - |
| HTC_SUMP | Hearth wall thermocouple (sump, 0.35 m from cold face) | Thermal/cooling | degC | ANALOG | 120.0 | 0 - 1200 | - | - | - |
| HTC_BOTTOM | Hearth bottom thermocouple | Thermal/cooling | degC | ANALOG | 120.0 | 0 - 1200 | - | - | - |
| CAST_ACTIVE | Cast in progress | Cast-house/tapping | - | EVENT | 120.0 | - | - | - | - |
| HMT_DIP | Hot metal temperature (dip, during cast) | Cast-house/tapping | degC | LAB | 1800 | 1350 - 1650 | - | - | hmt_C |
| HM_SI | Hot metal Si (lab) | Cast-house/tapping | wt% | LAB | 5400 | 0 - 2.0 | - | 25 | hm_si_pct |
| HM_S | Hot metal S (lab) | Cast-house/tapping | wt% | LAB | 5400 | 0 - 0.2 | - | 25 | hm_s_pct |
| HM_C | Hot metal C (lab) | Cast-house/tapping | wt% | LAB | 5400 | 3 - 6 | - | 25 | hm_c_pct |
| HM_MN | Hot metal Mn (lab) | Cast-house/tapping | wt% | LAB | 5400 | 0 - 2 | - | 25 | hm_mn_pct |
| HM_P | Hot metal P (lab) | Cast-house/tapping | wt% | LAB | 5400 | 0 - 0.5 | - | 25 | hm_p_pct |
| SLAG_B2 | Slag basicity CaO/SiO2 (lab) | Cast-house/tapping | - | LAB | 5400 | 0.6 - 2.0 | - | 30 | slag_B2 |
| SLAG_AL2O3 | Slag Al2O3 (lab) | Cast-house/tapping | wt% | LAB | 5400 | 0 - 30 | - | 30 | slag_Al2O3_pct |
| SLAG_FEO | Slag FeO (lab) | Cast-house/tapping | wt% | LAB | 5400 | 0 - 10 | - | 30 | slag_FeO_pct |
| HM_CAST_T | Hot metal tapped per cast (torpedo weighing) | Cast-house/tapping | t | EVENT | 120.0 | - | - | - | - |
| STOCKLINE | Stockline (radar), depth below zero level | Charging/burden | m | ANALOG | 120.0 | 0 - 6 | - | - | - |
| CHARGES_H | Charges per hour | Charging/burden | 1/h | CALC | 120.0 | - | - | - | - |
| COKE_BATCH | Coke batch weight (wet) | Charging/burden | t | SETPOINT | 120.0 | - | - | - | coke_batch_t (MV) |
| ORE_BATCH | Ore batch weight (wet) | Charging/burden | t | SETPOINT | 120.0 | - | - | - | ore_batch_t (MV) |
| STOVE_CHANGE | Stove change-over | Equipment events | - | EVENT | 120.0 | - | - | - | - |
| O2_TRIP | Oxygen supply interruption | Equipment events | - | EVENT | 120.0 | - | - | - | - |

## Tag mapping template

`docs/tag_mapping_template.csv` lists every catalogue tag with an empty `site_tag` column. Fill in the site's historian or OPC UA tag names, site units and, where no automatic conversion exists, scale and offset; then post the rows to `POST /api/v1/tags/map` (PROCESS_ENGINEER) or enter them on the Data and sensors page. Mark tags that are not installed so they are reported as UNAVAILABLE instead of MISSING.

## Published twin variables (stream and history)

These model outputs are streamed on every tick and stored in the twin history (`GET /api/v1/twin/history`). They are MODEL or DERIVED values, never measurements:

`production_t_d`, `hmt_C`, `hm_si_pct`, `hm_s_pct`, `fuel_rate_kg_tHM`, `coke_rate_kg_tHM`, `pci_rate_kg_tHM`, `eta_CO_pct`, `top_gas_T_C`, `pressure_drop_kPa`, `blast_pressure_kPa_g`, `raft_C`, `slag_rate_kg_tHM`, `slag_B2`, `dripping_zone_height_m`, `heat_loss_total_MW`, `active_coke_T_C`, `productivity_t_m3_d`, `hm_c_pct`, `slag_FeO_pct`, `top_CO_pct`, `top_CO2_pct`, `top_H2_pct`, `solution_loss_kgC_tHM`, `hearth_metal_level_m`, `hearth_slag_level_m`, `blast_volume_Nm3_min`, `blast_temp_C`, `o2_enrichment_pct`, `pci_t_h`, `blast_moisture_g_Nm3`, `thermal_reserve_T_C`, `hmt_corrected_C`, `si_corrected_pct`, `top_gas_T_uptake_C`.

## Exported files

| Export | Content | Provenance header |
|---|---|---|
| `/reports/export?kind=balance` | element balance table, energy balance, conventional heat balance | data origin, config/model version, UTC, input hash |
| `kind=kpis` | current KPIs with units and provenance | same |
| `kind=telemetry` | received tag values with quality, last 24 h | same |
| `kind=streams` | every input and output stream: weights per hour and per tHM, compositions, temperatures | same |
| scenario CSV (UI) | baseline and case trajectories of all KPIs | data origin SYNTHETIC, scenario id, input hash |
