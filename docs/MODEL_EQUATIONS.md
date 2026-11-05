# Model equations

This document states what the physics twin actually computes, with the correlations and data
sources used in the code. File references are to `app/physics/`. Everything listed as
*provisional* is a calibration choice for the illustrative furnaces, not a validated plant
parameter; see [MODEL_VALIDATION.md](MODEL_VALIDATION.md) and [LIMITATIONS.md](LIMITATIONS.md).

## 1. Domain, state and numerics (`integrator.py`, `zones.py`)

The furnace is described from the stockline to the hearth bottom by the configured geometry
(throat, stack, belly, bosh, hearth; diameters and heights are inputs, volumes are derived in
`zones.volumes`).

| Fidelity | Discretisation | Use |
|---|---|---|
| `fast` (default) | 14 axial finite-volume cells, 1 radial sector, `dt` 120 s (live) / 240 s (studies) | live twin, observer, MPC identification, studies |
| `hifi` | 28 axial x 3 radial sectors | background jobs only (warm start inside the job) |

State per radial sector *r* and axial cell *j* (j = 0 just above the tuyeres):

- `s_n[r,j,k]` moles of every solid species (33 ore- and coke-carrier species; 13 gas and 16 liquid species elsewhere),
- `s_H[r,j]` absolute solid enthalpy [J] (formation + sensible); temperature by inversion of h(T),
- `s_V[r,j,m]` bulk volume by charged material *m* (sinter, pellet, lump, coke, HBI ...),
- the deadman / active-coke node, the hearth pools (metal and slag moles, enthalpy) and every
  refractory layer temperature of every wall segment.

The gas phase is quasi-steady (residence time of seconds against hours for solids) and is
marched upward from the raceway each step. Solids descend by a donor-cell (upwind) volume
transport, sub-cycled to respect the Courant limit; the volume freed by melting, coke
gasification and raceway consumption drives the descent.

**Conservation by construction.** Every species transfer is applied with its balanced
stoichiometry (checked for every reaction by `tests/test_stoichiometry.py`), every heat
exchange is booked on both sides, and positivity limiters cap an extent at the available
inventory. The per-step element and energy residuals are therefore round-off level; they are
computed and published every step:

$$ r_e = \frac{\left| \Delta N_e^{\text{inventory}} - (N_e^{\text{in}} - N_e^{\text{out}}) \right|}{\max(N_e^{\text{throughput}}, 1\ \text{mol})} $$

for each of the 15 tracked elements (Fe, C, O, H, N, Si, Al, Ca, Mg, Mn, P, S, K, Na, Zn), and

$$ r_E = \frac{\left| \Delta H^{\text{inventory}} - (\dot H^{\text{in}} - \dot H^{\text{out}} - Q^{\text{walls}} - Q^{\text{tuyeres}})\,\Delta t \right|}{H^{\text{throughput}}} $$

(`material_balance.max_rel_residual`, `energy_balance.py`). The 1-mol floor keeps trace
elements with near-zero throughput from producing meaningless relative values.

## 2. Thermochemistry (`species.py`, `thermodynamics.py`)

Absolute molar enthalpy of every species:

$$ h_k(T) = \Delta_f H^\circ_{k,298} + \int_{298.15}^{T} c_{p,k}\, dT + h_{\text{add},k} $$

$$ c_p = a + b\,10^{-3} T + c\,10^{5} T^{-2} + d\,10^{-6} T^{2} \quad [\text{J mol}^{-1}\text{K}^{-1}] $$

with Kubaschewski-type coefficients ([KAS] Kubaschewski, Alcock & Spencer 1993; [JANAF] Chase
1998; carbonates and goethite from Robie & Hemingway 1995). Dissolved C, Si, Mn, P and S carry
their heats of solution in liquid iron (Sigworth & Elliott 1974) in `h_add`. Because reaction
heat is the difference of absolute enthalpies, no separate heat-of-reaction term exists and
nothing can be counted twice.

Gas-solid equilibria that set the driving forces use log10 K = A/T + B correlations for the
Fe-O-C-H system (consistent with the Baur-Glaessner diagram, wuestite treated as FeO):
hematite, magnetite and wuestite reduction by CO and H2, Boudouard (C + CO2 = 2CO), water-gas
(C + H2O = CO + H2), water-gas shift, calcite and magnesite decomposition. The Baur-Glaessner
and Ellingham diagrams in the UI are drawn from the same functions (`/api/v1/thermo/*`).

## 3. Reaction network and kinetics (`reactions.py`)

Rate laws are lumped engineering forms per computational cell (mol/s):

$$ k(T) = k_{\text{ref}} \exp\!\left[-\frac{E}{R}\left(\frac{1}{T} - \frac{1}{T_{\text{ref}}}\right)\right] $$

| Group | Reactions | Rate form |
|---|---|---|
| Indirect reduction | Fe2O3 -> Fe3O4 -> FeO -> Fe by CO and by H2 (6 reactions) | r = k f_size f_RI n_ox DF / D(X) |
| Carbon gasification | C(coke) + CO2 (solution loss), C + H2O, char + CO2/H2O | r = k f_CRI n_C DF |
| Shift | CO + H2O = CO2 + H2 | relaxation to equilibrium |
| Direct reduction (solid, high T) | Fe2O3 / Fe3O4 + C -> FeO + CO above 900 degC | r = k n |
| Decomposition | CaCO3, MgCO3 (r = k n (1 - p_CO2/p_eq)), goethite, volatile matter, burden and coke moisture | first order |
| Volatiles | ZnO + CO -> Zn(g); SiO(g) re-oxidation in the shaft; alkalis | first order / fraction |
| Dripping zone | (FeO) + C -> Fe + CO; carburisation; SiO + [C] -> [Si] + CO; (MnO), (P2O5) reduction; COS absorption into slag and metal | conversion 1 - exp(-k tau) or partition |
| Hearth | [S] + (CaO) + [C] -> (CaS) + CO; [Si] + 2(FeO) -> (SiO2) + 2Fe; [Si] + 2(MnO); (MnO) + [C] | relaxation to partition |
| Raceway | 2C + O2 -> 2CO; C + H2O; CH4; SiO generation from ash; S -> COS; ash Fe2O3 | complete (oxygen-limited) |

Driving force for reduction by reductant *red* with product *ox* (`integrator._gas_march`):

$$ x_{eq} = \max\!\left(\frac{K F_{red} - F_{ox}}{1+K}, 0\right), \qquad DF = x_{eq}/F_t $$

Mixed control with product-layer diffusion (shrinking-core form):

$$ D(X) = 1 + \beta\left[(1-X)^{-1/3} - 1\right] $$

where X is the local stage conversion. Every extent is additionally capped at 95 % of the
equilibrium extent of the local gas (no overshoot) and at the available inventory.

Activation energies and reference rate constants are tabulated with their sources in the
code and in `/api/v1/thermo/kinetics`. All kinetic constants are *provisional*: they are
calibrated so the illustrative furnaces reproduce typical gas utilisation (45-51 %), direct
reduction degree and solution loss (75-90 kgC/tHM), and must be re-identified on plant data.

## 4. Gas flow and permeability (`gas_flow.py`)

Layered ore/coke bed, Ergun equation per layer type:

$$ \frac{\Delta P}{L} = 150\,\frac{\mu (1-\varepsilon)^2 u}{\varepsilon^3 (\phi d)^2} + 1.75\,\frac{\rho (1-\varepsilon) u^2}{\varepsilon^3 \phi d} $$

with ore and coke layers weighted by their volume fractions, ore voidage reduced with the
softening index and an effective velocity raised where softened ore blocks the cross-section
(`bed_pressure_gradient`). Gas viscosity and conductivity are mixture-weighted with
temperature scaling. Hydraulic risk indicators: flooding index (Sherwood-type flooding
correlation for the dripping zone), fluidisation margin at the stockline (Wen & Yu 1966),
permeability index (blast volume / dP) and resistance index K.

Gas-solid heat transfer coefficient (Wakao-type packed-bed correlation):

$$ Nu = 2 + 1.1\,Re^{0.6}\,Pr^{1/3}, \qquad h_{gs} = Nu\,k_g/d_p $$

## 5. Raceway and tuyeres (`raceway.py`)

Oxygen-starved net raceway chemistry: every O atom of blast, enrichment and moisture leaves as
CO (or COS / SiO), all H as H2, all N as N2. Carbon comes from burnt PCI (burnout
eta = eta_max (1 - exp(-k lambda)), lambda the oxygen-to-PCI-carbon ratio), injected CH4 and
finally from deadman coke. PCI enthalpy uses its higher heating value, so devolatilisation
heat is implicit. The raceway gas temperature solves the enthalpy balance after tuyere
cooling losses; **RAFT** is reported on the conventional definition (coke entering at 1500
degC, no SiO, no tuyere loss) and is labelled an estimate. Diagnostics: tuyere velocity, blast
kinetic energy per tuyere, raceway depth (provisional correlation), bosh gas volume and
composition.

## 6. Cohesive zone, dripping zone and deadman (`dripping.py`, `integrator.py`)

Each ore material has a softening and a melting temperature. The softening index of a cell
rises from 0 at T_soft to 1 at T_melt; the melted fraction leaves the solid cells as primary
metal and primary slag at temperature clip(T, T_m, T_m + superheat). The cohesive-zone
boundaries reported are the cells with softening index between 0.02 and 0.98 (*low
confidence*: a temperature-window proxy).

Dripping-zone height is continuous in the state:

$$ h_{drip} = \sum_j \Delta z_j \prod_{i \le j} g(x_i), \qquad g(x) = \text{clip}(1 - x/0.3, 0, 1) $$

with x_i the ore volume fraction of cell i counted upward from the tuyeres. The active-coke /
deadman node exchanges heat between rising raceway gas, falling melts and coke with
effectiveness-NTU relations (`drip_ntu_liq`), and performs the dripping-zone reactions of
section 3 with exact stoichiometry; its enthalpy closes the balance exactly.

## 7. Hearth (`hearth.py`, `slag_metal.py`)

Two liquid pools (metal, slag) in the porous deadman with porosity epsilon, heat exchange to
the deadman coke and to the hearth walls/bottom (wall model of section 8), and the hearth
reactions of section 3. Sulfur partition:

$$ \log L_S = \log C_S + \log f_S - \log K_S - \tfrac{1}{2}\log p_{O_2} $$

with the sulfide capacity from optical basicity (Sosinsky & Sommerville 1986),
log K_S = 7054.6/T - 1.224, p_O2 from C/CO equilibrium and log f_S from first-order
interaction parameters. Carbon saturation follows the Neumann relation
C_sat = 1.30 + 2.57e-3 T - 0.31 Si - 0.33 P - 0.40 S + 0.028 Mn (wt%, T in degC). Slag
viscosity uses the Urbain model; liquidus is an estimate.

**Casting.** A cast opens when the liquid top is `open_level_above_taphole_m` above the
taphole and the minimum gap has elapsed; outflow through the taphole follows an orifice
relation driven by top pressure plus the liquid head,

$$ v = C_d \sqrt{2\,\Delta P/\rho}, \qquad Q = v\,\pi d_{th}^2/4, $$

with the taphole diameter eroding during the cast. The metal fraction of the outflow is the
share of a drainage window (1 m) around the taphole axis that lies below the slag/metal
interface. The cast ends when the hearth runs dry or at the maximum duration. Each cast emits
CAST_START / SAMPLE / CAST_END events with tapped tonnage, temperature and compositions (the
sample is the *lab* value, delayed and noisy, in the synthetic plant).

## 8. Walls, refractory and cooling (`heat_transfer.py`)

Every wall zone (shaft zones, sump wall, upper hearth wall, hearth bottom) is a 1-D
finite-volume conduction problem through its layers (skull/accretion, refractory grades,
stave, grout, shell), cylindrical for walls and planar for the bottom, solved implicitly
(backward Euler; the discrete energy balance closes exactly):

$$ C_i \frac{dT_i}{dt} = G_{i-1,i}(T_{i-1}-T_i) - G_{i,i+1}(T_i - T_{i+1}) $$

Boundary conditions:

- process side (Robin): q_in = h_in A_in (T_process - T_hotface), h_in per zone (gas
  convection + radiation lumped; liquid-side values in the hearth);
- cooling water: q_w = h_w phi_pipe A_out (T_cold - T_water) with Dittus-Boelter
  Nu = 0.023 Re^0.8 Pr^0.4 from the configured flow, pipe diameter and count;
- shell: q_sh = (T_cold - T_amb) / (R_grout + R_shell + 1/((h_nc + h_rad) A)) with
  h_rad = eps sigma (T_s^2 + T_a^2)(T_s + T_a) (linearised, lagged one step) and turbulent
  natural convection h_nc = 1.31 (T_s - T_a)^(1/3).

Material properties (k, rho, cp) are listed by `/api/v1/thermo/refractories` and edited per
layer in the configuration page. A remaining-lining estimate locates the 1150 degC isotherm.
Tuyere cooling loss scales with the raceway temperature.

## 9. Charging and burden distribution (`burden.py`)

Charges (coke batch, ore batch with the configured sinter / pellet / lump / HBI mix, fluxes)
are dumped when the stockline falls below its set depth. A bell-less-top radial distribution
splits each dump across radial sectors (hi-fi). A layer tracker follows the coke and ore
layers down the shaft for display; it is anchored at the stockline.

## 10. Outputs

KPIs (`outputs.kpis`) carry a unit and a provenance (MODEL, DERIVED, PROXY). Specific
consumptions use 4-hour windows; the coke and ore rates are computed over whole charge
cycles inside the window and divided by the hot metal produced in the same interval, which
removes the one-charge quantisation of a fixed time window. Also published: axial/radial
profiles, field payloads for the 2-D/3-D displays, stream compositions and weights of every
input and output (sinter, pellet, lump, coke, PCI, blast; hot metal, slag, top gas, dust),
conventional heat balance (Hess-type supply/demand table and Sankey), Rist operating line,
carbon balance by boundary, cumulative input/output tonnages, and the element/energy residuals.

## 11. Estimation, forecasting and control

These are documented separately:

- observer (parameter EKF with Ornstein-Uhlenbeck reversion, Joseph-form update, Huber-type
  admission) and WLS reconciliation: [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) section 4;
- hybrid ML forecasting with OOD gate and conformal intervals: [MODEL_VALIDATION.md](MODEL_VALIDATION.md);
- MPC formulation: [MPC_CONTROL_SPECIFICATION.md](MPC_CONTROL_SPECIFICATION.md).

## References

- Chase, M.W. NIST-JANAF Thermochemical Tables, 4th ed., J. Phys. Chem. Ref. Data Monograph 9 (1998).
- Kubaschewski, O., Alcock, C.B., Spencer, P.J. Materials Thermochemistry, 6th ed., Pergamon (1993).
- Robie, R.A., Hemingway, B.S. USGS Bulletin 2131 (1995).
- Sigworth, G.K., Elliott, J.F. Metal Science 8 (1974) 298-310.
- McKewan, W.M. Trans. TMS-AIME 218 (1960) 2-6; von Bogdandy, L., Engell, H.-J. The Reduction of Iron Ores, Springer (1971).
- Ergun, S. Chem. Eng. Prog. 48 (1952) 89-94.
- Wakao, N., Kaguei, S. Heat and Mass Transfer in Packed Beds, Gordon & Breach (1982).
- Wen, C.Y., Yu, Y.H. AIChE J. 12 (1966) 610-612.
- Sosinsky, D.J., Sommerville, I.D. Metall. Trans. B 17 (1986) 331-337.
- Urbain, G. Steel Research 58 (1987) 111-116.
- Rist, A., Meysson, N. J. Metals 19 (1967) 50-59.
- Geerdes, M., Chaigneau, R., Lingiardi, O. Modern Blast Furnace Ironmaking, 4th ed., IOS Press (2020) - operating ranges used for calibration targets.
