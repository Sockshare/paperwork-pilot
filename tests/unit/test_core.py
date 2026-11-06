"""G1 — unit tests: rules engine, normalizer, JSON repair, extraction, config."""

import io
import json

import pytest

from paperwork_pilot.analysis.rules import analyze_rules, detect_type, merge_safety_net
from paperwork_pilot.analysis.schema import normalize
from paperwork_pilot.config import load_settings, save_settings, update_settings
from paperwork_pilot.extract import extract_text
from paperwork_pilot.llm import extract_json


# ---------------- rules engine ----------------
@pytest.mark.parametrize(
    "name,expected",
    [
        ("lease.txt", "lease"),
        ("medical_bill.txt", "medical_bill"),
        ("insurance_denial.txt", "insurance"),
        ("job_offer.txt", "job_offer"),
        ("debt_collection.txt", "debt"),
        ("subscription_terms.txt", "terms"),
    ],
)
def test_detect_type_on_samples(samples_dir, name, expected):
    text = (samples_dir / name).read_text()
    assert detect_type(text) == expected


def test_rules_lease_finds_key_items(samples_dir):
    r = analyze_rules((samples_dir / "lease.txt").read_text())
    titles = {w["title"] for w in r["watch_outs"]}
    assert "Automatic renewal" in titles
    assert "Late fee" in titles
    assert any("arbitration" in t.lower() for t in titles)
    assert any("$1,450.00" in m["amount"] for m in r["money"])
    assert any("August 25, 2026" in d["date"] for d in r["deadlines"])
    assert r["plain_summary"]
    assert r["confidence"] == "low"


def test_rules_debt_letter(samples_dir):
    r = analyze_rules((samples_dir / "debt_collection.txt").read_text())
    assert r["document_type"] == "debt"
    assert any("December 6, 2026" in d["date"] for d in r["deadlines"])
    assert any(g["term"] in ("original creditor", "dispute", "settlement") for g in r["glossary"])


def test_rules_never_crash_on_garbage():
    for text in ["", "x", "1234 $$$ ///", "\n\n\n", "a" * 50000]:
        r = analyze_rules(text)
        assert "plain_summary" in r


# ---------------- normalizer ----------------
def test_normalize_handles_missing_and_wrong_types():
    r = normalize({"document_type": "Rental Agreement", "deadlines": "pay by Friday; move out by June", "money": None, "watch_outs": ["Late fee"], "glossary": "deposit: money held"})
    assert r["document_type"] == "lease"
    assert len(r["deadlines"]) == 2
    assert r["money"] == []
    assert r["watch_outs"][0]["title"] == "Late fee"
    assert r["glossary"][0]["term"] == "deposit"


def test_normalize_sorts_watch_outs_high_first():
    r = normalize({"watch_outs": [{"title": "a", "severity": "low"}, {"title": "b", "severity": "high"}, {"title": "c", "severity": "bogus"}]})
    assert [w["severity"] for w in r["watch_outs"]] == ["high", "medium", "low"]


def test_normalize_none():
    r = normalize(None)
    assert r["document_type"] == "other" and r["title"]


# ---------------- safety net merge ----------------
def test_merge_safety_net_adds_missing_dates(samples_dir):
    text = (samples_dir / "lease.txt").read_text()
    rules = analyze_rules(text)
    ai = normalize({"plain_summary": "A lease.", "deadlines": [], "money": []})
    merged = merge_safety_net(ai, rules)
    assert merged["deadlines"] and merged["money"]
    assert merged["plain_summary"] == "A lease."


# ---------------- JSON repair ----------------
@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Sure! Here is the JSON:\n{"a": 1}\nHope that helps.',
        '{"a": 1, "b": [1,2,],}',
    ],
)
def test_extract_json_variants(raw):
    assert extract_json(raw) == {"a": 1} or extract_json(raw) == {"a": 1, "b": [1, 2]}


def test_extract_json_garbage():
    assert extract_json("no json here") is None
    assert extract_json("") is None
    assert extract_json("[1,2,3]") is None


# ---------------- extraction ----------------
def test_extract_txt():
    r = extract_text("a.txt", b"Hello there.\n\n\n\nThis is a test.")
    assert r.ok and "Hello there." in r.text and "\n\n\n" not in r.text


def test_extract_unsupported():
    r = extract_text("a.exe", b"xx")
    assert not r.ok and "not supported" in r.error


def test_extract_empty():
    assert not extract_text("a.txt", b"").ok


def test_extract_docx():
    import docx

    d = docx.Document()
    d.add_paragraph("Rent is $900 per month.")
    buf = io.BytesIO()
    d.save(buf)
    r = extract_text("lease.docx", buf.getvalue())
    assert r.ok and "$900" in r.text


def test_extract_pdf_text_layer():
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=300, height=300)
    buf = io.BytesIO()
    w.write(buf)
    r = extract_text("blank.pdf", buf.getvalue())
    # A blank PDF has no text -> friendly error, not a crash
    assert not r.ok and "scan" in r.error.lower()


def test_extract_latin1_not_mangled():
    data = "Rent is due. Total: £1,200 — café".encode("cp1252")
    r = extract_text("letter.txt", data)
    assert r.ok and "£1,200" in r.text and "café" in r.text


def test_extract_utf8_bom_stripped():
    r = extract_text("a.txt", "\ufeffHello there, this is a letter.".encode("utf-8"))
    assert r.ok and r.text.startswith("Hello")


def test_extract_docx_bomb_rejected():
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "<w:p>" + "A" * (70 * 1024 * 1024) + "</w:p>")
    r = extract_text("bomb.docx", buf.getvalue())
    assert not r.ok and "too large" in r.error


def test_extract_encrypted_pdf_message():
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.encrypt("secret")
    buf = io.BytesIO()
    w.write(buf)
    r = extract_text("locked.pdf", buf.getvalue())
    assert not r.ok and "password" in r.error.lower()


def test_normalize_never_raises_on_weird_nested_values():
    r = normalize({"your_options": [{"text": 5}, {"step": {"a": 1}}], "deadlines": [{"date": 3, "what": None}], "glossary": [{"term": ["x"], "meaning": 7}]})
    assert isinstance(r["your_options"], list) and r["deadlines"][0]["date"] == "3"


def test_sept_dates_parse():
    from paperwork_pilot.analysis.rules import _parse_date

    assert _parse_date("Sept 5, 2027") is not None
    assert _parse_date("Nov. 3, 2027") is not None


def test_extract_image_without_tesseract(monkeypatch):
    import paperwork_pilot.extract as ex

    monkeypatch.setattr(ex, "_has_tesseract", lambda: False)
    r = extract_text("photo.jpg", b"\xff\xd8\xff")
    assert not r.ok and "Tesseract" in r.error


# ---------------- config ----------------
def test_settings_roundtrip_and_masking():
    s = load_settings()
    assert s.provider == "offline"
    s2 = update_settings({"provider": "openai", "openai_api_key": "sk-abcdefghijklmnop", "model": "gpt-4o-mini"})
    assert s2.openai_api_key == "sk-abcdefghijklmnop"
    pub = s2.public_dict()
    assert "*" in pub["openai_api_key"] and pub["has_openai_key"]
    # masked key sent back must not overwrite the real one
    s3 = update_settings({"openai_api_key": pub["openai_api_key"]})
    assert s3.openai_api_key == "sk-abcdefghijklmnop"


def test_settings_bad_provider_rejected():
    from paperwork_pilot.config import SettingsError

    with pytest.raises(SettingsError):
        update_settings({"provider": "skynet"})
    assert load_settings().provider == "offline"


def test_settings_survive_corrupt_file(tmp_path, monkeypatch):
    from paperwork_pilot import config

    config.config_path().write_text("{not json")
    s = load_settings()
    assert s.provider == "offline"
