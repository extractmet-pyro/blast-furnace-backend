"""CSV / Excel historian import with explicit mapping, units, timestamps and quality.

Rules enforced (each violation is reported, never silently fixed):
  * a timestamp column `t_utc` (ISO-8601, UTC 'Z' or +00:00) is mandatory and must be strictly
    increasing; duplicate or out-of-order rows are rejected with their row numbers;
  * every other column must be a catalogue tag or a mapped site tag (POST /api/v1/tags/map);
    unmapped columns are listed and ignored, catalogue tags absent from the file are listed as
    MISSING (they are never filled with zeros);
  * values must be numeric and finite; spreadsheet formulas / formula-injection payloads
    (cells starting with '=', '+', '-', '@' followed by letters, or tab/CR) are rejected;
  * laboratory columns may carry a sampling-time column `<TAG>__t_sample` (ISO UTC);
  * file size is capped (MAX_UPLOAD_MB) and macro-enabled workbooks (.xlsm) are refused.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .synthetic import EPOCH
from .tag_mapping import TAG_INDEX, TagMapper

FORMULA = re.compile(r"^\s*[=+@\t\r]|^\s*-[A-Za-z(]")


class UploadError(ValueError):
    def __init__(self, message: str, details=None):
        super().__init__(message)
        self.details = details


def _read(content: bytes, filename: str) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith(".xlsm") or name.endswith(".xlsb"):
        raise UploadError("Macro-enabled/binary workbooks are not accepted (.xlsm/.xlsb). Export to .xlsx or .csv.")
    if name.endswith(".csv") or name.endswith(".csv.gz"):
        comp = "gzip" if name.endswith(".gz") else None
        return pd.read_csv(io.BytesIO(content), dtype=str, compression=comp, keep_default_na=False)
    if name.endswith(".xlsx"):
        return pd.read_excel(io.BytesIO(content), dtype=str, engine="openpyxl", keep_default_na=False)
    raise UploadError("Unsupported file type (use .csv, .csv.gz or .xlsx)")


def parse_history(content: bytes, filename: str, mapper: Optional[TagMapper] = None,
                  max_rows: int = 200_000) -> Tuple[pd.DataFrame, Dict[str, object]]:
    raw = _read(content, filename)
    if len(raw) > max_rows:
        raise UploadError(f"File has {len(raw)} rows; limit is {max_rows}")
    if "t_utc" not in raw.columns:
        raise UploadError("Missing mandatory timestamp column 't_utc' (ISO-8601 UTC)")
    # formula / injection screening on every cell
    bad = []
    for c in raw.columns:
        col = raw[c].astype(str)
        hits = col[col.str.match(FORMULA)]
        for idx in hits.index[:5]:
            bad.append({"row": int(idx) + 2, "column": c, "value": str(hits[idx])[:40]})
    if bad:
        raise UploadError("Spreadsheet formulas / injection payloads are not accepted", bad[:20])
    ts = []
    errs = []
    for i, s in enumerate(raw["t_utc"].astype(str)):
        s2 = s.strip()
        if not (s2.endswith("Z") or s2.endswith("+00:00")):
            errs.append({"row": i + 2, "reason": "timestamp not marked UTC"})
            ts.append(np.nan)
            continue
        try:
            ts.append(datetime.fromisoformat(s2.replace("Z", "+00:00")).timestamp() - EPOCH.timestamp())
        except ValueError:
            errs.append({"row": i + 2, "reason": "unparseable timestamp"})
            ts.append(np.nan)
    if errs:
        raise UploadError("Invalid timestamps", errs[:20])
    t = np.array(ts, dtype=float)
    nonmono = np.where(np.diff(t) <= 0)[0]
    if nonmono.size:
        raise UploadError("Timestamps must be strictly increasing (duplicates/out-of-order rows found)",
                          [{"row": int(i) + 3} for i in nonmono[:20]])
    out = pd.DataFrame({"t_s": t, "t_utc": raw["t_utc"].str.strip()})
    mapped, unmapped, conversions = [], [], {}
    for c in raw.columns:
        if c == "t_utc":
            continue
        base = c[:-len("__t_sample")] if c.endswith("__t_sample") else c
        if base in TAG_INDEX:
            target, scale, offset = base, 1.0, 0.0
        elif mapper and base in mapper.mapping:
            m = mapper.mapping[base]
            target, scale, offset = m["tag"], m["scale"], m["offset"]
            conversions[c] = {"to": target, "scale": scale, "offset": offset, "site_unit": m["unit"]}
        else:
            unmapped.append(c)
            continue
        if c.endswith("__t_sample"):
            vals = []
            for s in raw[c].astype(str):
                s = s.strip()
                vals.append(np.nan if not s else datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() - EPOCH.timestamp())
            out[target + "__t_sample"] = vals
            continue
        num = pd.to_numeric(raw[c].replace("", np.nan), errors="coerce")
        nonnum = raw[c][(num.isna()) & (raw[c].astype(str).str.strip() != "")]
        if len(nonnum):
            raise UploadError(f"Column '{c}' contains non-numeric values", [{"row": int(i) + 2, "value": str(v)[:30]}
                                                                         for i, v in nonnum.head(10).items()])
        if not np.all(np.isfinite(num.dropna())):
            raise UploadError(f"Column '{c}' contains non-finite values")
        out[target] = num * scale + offset
        mapped.append(target)
    installed = [td.tag for td in TAG_INDEX.values() if td.installed and td.signal_type != "CALC"]
    missing = [t for t in installed if t not in out.columns]
    summary = {"rows": int(len(out)), "start_utc": str(out["t_utc"].iloc[0]) if len(out) else None,
               "end_utc": str(out["t_utc"].iloc[-1]) if len(out) else None, "mapped_columns": sorted(set(mapped)),
               "unmapped_columns_ignored": unmapped, "catalogue_tags_missing": missing, "conversions": conversions,
               "note": "Missing tags are reported as MISSING downstream - never replaced with zeros."}
    return out, summary


def frame_to_csv_bytes(df: pd.DataFrame, meta: Dict[str, str]) -> bytes:
    """CSV export with a provenance header (config/model revision, UTC, units, input hash, label)."""
    head = "".join(f"# {k}: {v}\n" for k, v in meta.items())
    return (head + df.to_csv(index=False)).encode()


def frame_to_xlsx_bytes(sheets: Dict[str, pd.DataFrame], meta: Dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"key": list(meta.keys()), "value": list(meta.values())}).to_excel(xw, sheet_name="provenance", index=False)
        for name, df in sheets.items():
            safe = df.copy()
            for c in safe.columns:  # neutralise any text that a spreadsheet could interpret as a formula
                if safe[c].dtype == object:
                    safe[c] = safe[c].map(lambda x: ("'" + x) if isinstance(x, str) and FORMULA.match(x) else x)
            safe.to_excel(xw, sheet_name=name[:31], index=False)
    return buf.getvalue()
