"""Data quality rules, missing/uninstalled tags never become zero, historian upload validation."""
import pytest

from app.data.csv_excel import UploadError, parse_history
from app.data.quality import QualityEngine
from app.data.tag_mapping import TagMapper


def _r(tag, v, ts):
    return {"tag": tag, "value": v, "unit": "", "ts_sensor": ts, "ts_ingest": ts, "source": "TEST"}


def test_range_and_missing():
    q = QualityEngine()
    out = q.assess([_r("BL_TEMP", 2500.0, "2026-11-04T00:00:00Z"), _r("TG_CO", None, "2026-11-04T00:00:00Z")],
                   "2026-11-04T00:00:00Z")
    by = {o["tag"]: o for o in out}
    assert by["BL_TEMP"]["quality"] == "BAD"
    assert by["TG_CO"]["quality"] == "MISSING" and by["TG_CO"]["value"] is None


def test_uninstalled_tags_reported_unavailable_not_zero():
    q = QualityEngine()
    snap = {s["tag"]: s for s in q.snapshot("2026-11-04T00:00:00Z")}
    unavailable = [s for s in snap.values() if s["quality"] == "UNAVAILABLE"]
    assert unavailable, "catalogue must expose uninstalled instruments explicitly"
    assert all(s.get("value") is None for s in unavailable)


def test_rate_of_change_and_flatline():
    q = QualityEngine()
    q.assess([_r("BL_TEMP", 1180.0, "2026-11-04T00:00:00Z")], "2026-11-04T00:00:00Z")
    o = q.assess([_r("BL_TEMP", 1400.0, "2026-11-04T00:02:00Z")], "2026-11-04T00:02:00Z")[0]
    assert o["quality"] in ("SUSPECT", "BAD")
    q2 = QualityEngine()
    for i in range(40):
        o = q2.assess([_r("TG_TEMP", 150.0, f"2026-11-04T{i // 30:02d}:{(i * 2) % 60:02d}:00Z")], "2026-11-04T01:20:00Z")[0]
    assert o["quality"] == "SUSPECT" and "flat-line" in o["reasons"][0]


def test_stale_detection():
    q = QualityEngine()
    q.assess([_r("BL_FLOW", 6600.0, "2026-11-04T00:00:00Z")], "2026-11-04T00:00:00Z")
    snap = {s["tag"]: s for s in q.snapshot("2026-11-04T03:00:00Z")}
    assert snap["BL_FLOW"]["quality"] in ("STALE", "SUSPECT", "MISSING")


CSV_OK = "t_utc,BL_FLOW,BL_TEMP,SITE_HMT\n2026-11-01T00:00:00Z,6600,1180,1500\n2026-11-01T00:02:00Z,6610,1181,\n"


def test_upload_valid_with_mapping():
    m = TagMapper()
    m.set("SITE_HMT", "HMT_DIP", "degC", 1.0, 0.0)
    df, s = parse_history(CSV_OK.encode(), "h.csv", m)
    assert s["rows"] == 2 and "HMT_DIP" in s["mapped_columns"]
    assert "TG_CO" in s["catalogue_tags_missing"]                    # missing tags are listed, not zero-filled
    assert df["HMT_DIP"].isna().iloc[1]


def test_upload_unmapped_columns_listed():
    df, s = parse_history(CSV_OK.encode(), "h.csv", TagMapper())
    assert "SITE_HMT" in s["unmapped_columns_ignored"]


@pytest.mark.parametrize("bad,msg", [
    ("t_utc,BL_FLOW\n2026-11-01T00:00:00Z,=HYPERLINK(\"x\")\n", "formulas"),
    ("t_utc,BL_FLOW\n2026-11-01T00:00:00,6600\n", "timestamps"),
    ("t_utc,BL_FLOW\n2026-11-01T00:02:00Z,6600\n2026-11-01T00:00:00Z,6600\n", "increasing"),
    ("t_utc,BL_FLOW\n2026-11-01T00:00:00Z,abc\n", "non-numeric"),
    ("BL_FLOW\n6600\n", "t_utc"),
])
def test_upload_rejections(bad, msg):
    with pytest.raises(UploadError) as e:
        parse_history(bad.encode(), "h.csv", TagMapper())
    assert msg.lower() in str(e.value).lower()


def test_macro_workbook_refused():
    with pytest.raises(UploadError):
        parse_history(b"PK...", "evil.xlsm", TagMapper())


def test_event_tags_without_events_are_not_missing_and_not_scored():
    q = QualityEngine()
    snap = {s["tag"]: s for s in q.snapshot("2026-11-04T00:00:00Z")}
    assert snap["STOVE_CHANGE"]["quality"] == "NO_EVENT"
    sc = q.score("2026-11-04T00:00:00Z")
    assert "NO_EVENT" not in sc["counts"]


def test_tags_the_source_cannot_provide_are_unavailable_with_reason():
    q = QualityEngine()
    q.not_provided = {"ABP_T_CENTER": "not provided by the 1-D demo plant"}
    snap = {s["tag"]: s for s in q.snapshot("2026-11-04T00:00:00Z")}
    assert snap["ABP_T_CENTER"]["quality"] == "UNAVAILABLE"
    assert snap["ABP_T_CENTER"]["value"] is None and "1-D" in snap["ABP_T_CENTER"]["reasons"][0]
    assert "UNAVAILABLE" not in q.score("2026-11-04T00:00:00Z")["counts"]
