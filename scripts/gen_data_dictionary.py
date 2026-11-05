"""Generate docs/DATA_DICTIONARY.md and docs/tag_mapping_template.csv from the tag catalogue in code.

    python scripts/gen_data_dictionary.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.data.quality import CRITICAL_TAGS as CRITICAL  # noqa: E402
from app.data.tag_mapping import TAGS  # noqa: E402
from app.services.twin_service import STREAM_KPIS  # noqa: E402



def main() -> None:
    rows = [t.__dict__ for t in TAGS]
    with (ROOT / "docs" / "tag_mapping_template.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["site_tag", "catalogue_tag", "site_unit", "scale", "offset", "catalogue_unit", "description", "signal_type",
                    "expected_period_s", "installed_at_site(yes/no)", "comment"])
        for r in rows:
            w.writerow(["", r["tag"], r["unit"], 1, 0, r["unit"], r["description"], r["signal_type"], r["expected_period_s"], "", ""])
    out = ["# Data dictionary", "",
           "Generated from `app/data/tag_mapping.py` (`python scripts/gen_data_dictionary.py`). The same catalogue is served by "
           "`GET /api/v1/tags` with the current site mapping and installation status.", "",
           "Conventions",
           "- Timestamps are UTC (ISO 8601 with `Z`). Lab results carry their **sampling** time as well as the receipt time; the "
           "observer uses a lab value at its sampling time once it has been received.",
           "- Every value carries a quality of GOOD, SUSPECT, BAD, MISSING, UNAVAILABLE (not installed or not provided by the source) "
           "or NO_EVENT (event tags before their first event). Values are never replaced by zero.",
           "- Units are the catalogue units below. Site units are converted on ingest using the mapping (automatic for known "
           "pairs such as kPa(g) and bar(g); otherwise scale and offset).",
           "- `model_key` links a tag to the twin variable it is compared with; `mv` marks manipulated variables that drive the twin.",
           "- Critical tags (bold) must be GOOD for the advice trust gate to open, and count double in the data-quality score.", "",
           "| Tag | Description | Family | Unit | Signal | Period s | Range | Max rate /min | Lab delay min | Model key |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        tag = f"**{r['tag']}**" if r["tag"] in CRITICAL else r["tag"]
        rng = f"{r['lo']} - {r['hi']}" if r.get("lo") is not None else "-"
        mk = (r.get("model_key") or "-") + (" (MV)" if r.get("mv") else "")
        out.append(f"| {tag} | {r['description']} | {r['family']} | {r['unit']} | {r['signal_type']} | {r['expected_period_s']} | {rng} | "
                   f"{r.get('roc_max_per_min') or '-'} | {r.get('lab_delay_min') or '-'} | {mk} |")
    out += ["", "## Tag mapping template", "",
            "`docs/tag_mapping_template.csv` lists every catalogue tag with an empty `site_tag` column. Fill in the site's historian or "
            "OPC UA tag names, site units and, where no automatic conversion exists, scale and offset; then post the rows to "
            "`POST /api/v1/tags/map` (PROCESS_ENGINEER) or enter them on the Data and sensors page. Mark tags that are not "
            "installed so they are reported as UNAVAILABLE instead of MISSING.", "",
            "## Published twin variables (stream and history)", "",
            "These model outputs are streamed on every tick and stored in the twin history (`GET /api/v1/twin/history`). "
            "They are MODEL or DERIVED values, never measurements:", "",
            ", ".join(f"`{k}`" for k in STREAM_KPIS) + ", `hmt_corrected_C`, `si_corrected_pct`, `top_gas_T_uptake_C`.", "",
            "## Exported files", "",
            "| Export | Content | Provenance header |",
            "|---|---|---|",
            "| `/reports/export?kind=balance` | element balance table, energy balance, conventional heat balance | data origin, config/model version, UTC, input hash |",
            "| `kind=kpis` | current KPIs with units and provenance | same |",
            "| `kind=telemetry` | received tag values with quality, last 24 h | same |",
            "| `kind=streams` | every input and output stream: weights per hour and per tHM, compositions, temperatures | same |",
            "| scenario CSV (UI) | baseline and case trajectories of all KPIs | data origin SYNTHETIC, scenario id, input hash |", ""]
    (ROOT / "docs" / "DATA_DICTIONARY.md").write_text("\n".join(out))
    print(f"{len(rows)} tags")


if __name__ == "__main__":
    main()
