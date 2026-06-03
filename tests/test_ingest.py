from voiceagent.ingest.excel import ingest_file, map_headers, persist, write_rejected
from voiceagent.ingest.phone import infer_timezone, normalize_phone
from voiceagent.ingest.sample import make_sample


def test_normalize_phone_us():
    r = normalize_phone("(415) 555-2671")
    assert r.ok and r.e164 == "+14155552671"


def test_normalize_phone_float_string():
    assert normalize_phone("16502530000.0").e164 == "+16502530000"


def test_normalize_phone_rejects_bad():
    assert not normalize_phone("not-a-phone").ok
    assert not normalize_phone("").ok
    assert not normalize_phone("12345").ok


def test_infer_timezone():
    assert infer_timezone("+12125550199") == "America/New_York"
    assert infer_timezone("+14155552671") == "America/Los_Angeles"


def test_map_headers_aliases_and_drops_unknown():
    m = map_headers(["Full Name", "Cell", "Company", "TZ", "random col"])
    assert set(m.values()) >= {"name", "phone", "business_name", "timezone"}
    assert "random col" not in m


def test_ingest_sample(tmp_path):
    res = ingest_file(make_sample(tmp_path / "s.xlsx"), default_timezone="America/New_York")
    assert len(res.leads) >= 2
    assert res.duplicates_merged >= 1
    assert len(res.rejected) >= 3
    assert all(l.phone.startswith("+") for l in res.leads)
    assert any(l.timezone for l in res.leads)


def test_write_rejected(tmp_path):
    res = ingest_file(make_sample(tmp_path / "s.xlsx"))
    out = write_rejected(res.rejected, tmp_path / "r.xlsx")
    assert out and out.exists()


def test_persist_dedupes_against_db(session, tmp_path):
    res = ingest_file(make_sample(tmp_path / "s.xlsx"), default_timezone="America/New_York")
    first = persist(res, session)
    second = persist(res, session)  # idempotent re-run
    assert first["inserted"] >= 2
    assert second["inserted"] == 0
    assert second["skipped_existing"] == first["inserted"]
