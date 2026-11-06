"""G2 — API smoke tests, G6 — privacy test (no network in offline mode),
plus provider fallback tests with a fake AI."""

import socket

import pytest

from paperwork_pilot.analysis import analyze, ask_question, write_reply
from paperwork_pilot.config import load_settings
from paperwork_pilot.llm import ProviderError, providers


# ---------------- API smoke ----------------
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"]


def test_index_serves_ui(client):
    r = client.get("/")
    assert r.status_code == 200 and "Paperwork Pilot" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_status_shape(client):
    d = client.get("/api/status").json()
    assert d["provider"]["name"] == "offline" and d["provider"]["ok"]
    assert "reply_goals" in d and "samples" not in d
    assert "openai_api_key" in d["settings"]


def test_samples_listed_and_analyzable(client):
    samples = client.get("/api/samples").json()
    assert len(samples) >= 5
    for s in samples:
        r = client.post(f"/api/analyze/sample/{s['name']}")
        assert r.status_code == 200, s
        body = r.json()
        assert body["result"]["plain_summary"]
        assert body["result"]["meta"]["engine"] == "offline"
        assert body["id"]  # saved to history by default


def test_sample_404(client):
    assert client.post("/api/analyze/sample/nope.txt").status_code == 404


def test_analyze_text_and_too_short(client):
    assert client.post("/api/analyze/text", json={"text": "hi"}).status_code == 400
    r = client.post("/api/analyze/text", json={"text": "Your rent of $1,200 is due on March 1, 2027. A late fee of $50 applies after the 5th."})
    assert r.status_code == 200
    res = r.json()["result"]
    assert any("$1,200" in m["amount"] for m in res["money"])
    assert any("Late fee" == w["title"] for w in res["watch_outs"])


def test_analyze_file_txt_and_bad(client, samples_dir):
    data = (samples_dir / "job_offer.txt").read_bytes()
    r = client.post("/api/analyze/file", files={"file": ("offer.txt", data, "text/plain")})
    assert r.status_code == 200 and r.json()["result"]["document_type"] == "job_offer"
    r = client.post("/api/analyze/file", files={"file": ("virus.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400 and "not supported" in r.json()["detail"]


def test_file_too_big(client):
    big = b"a" * (25 * 1024 * 1024 + 1)
    r = client.post("/api/analyze/file", files={"file": ("big.txt", big, "text/plain")})
    assert r.status_code == 400 and "25 MB" in r.json()["detail"]


def test_reply_and_ask_offline(client):
    doc = client.post("/api/analyze/sample/medical_bill.txt").json()
    r = client.post("/api/reply", json={"result": doc["result"], "goal": "payment_plan", "tone": "polite", "details": "I lost my job in May.", "kind": "email"})
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "template" and "payment plan" in body["text"].lower() and "I lost my job" in body["text"]
    r = client.post("/api/ask", json={"text": doc["text"], "result": doc["result"], "question": "Is there a payment plan?"})
    assert r.status_code == 200 and "payment plan" in r.json()["answer"].lower()


def test_history_lifecycle(client):
    doc = client.post("/api/analyze/sample/lease.txt").json()
    items = client.get("/api/history").json()
    assert any(i["id"] == doc["id"] for i in items)
    got = client.get(f"/api/history/{doc['id']}").json()
    assert got["result"]["document_type"] == "lease"
    assert client.delete(f"/api/history/{doc['id']}").json()["deleted"] is True
    assert client.get(f"/api/history/{doc['id']}").status_code == 404
    client.post("/api/analyze/sample/lease.txt")
    assert client.delete("/api/history").json()["deleted"] >= 1
    assert client.get("/api/history").json() == []


def test_history_off(client):
    client.post("/api/settings", json={"save_history": False})
    doc = client.post("/api/analyze/sample/lease.txt").json()
    assert doc["id"] == ""
    assert client.get("/api/history").json() == []


def test_settings_endpoint_validation(client):
    assert client.post("/api/settings", json={"provider": "skynet"}).status_code == 400
    d = client.post("/api/settings", json={"provider": "offline", "setup_complete": True}).json()
    assert d["settings"]["setup_complete"] is True


def test_test_connection_without_key(client):
    d = client.post("/api/test-connection", json={"provider": "gemini"}).json()
    assert d["ok"] is False and "key" in d["message"].lower()


def test_foreign_host_header_is_rejected(client):
    """A web page using DNS rebinding would arrive with a non-local Host header."""
    from fastapi.testclient import TestClient

    from paperwork_pilot.server import app

    evil = TestClient(app, base_url="http://evil.attacker.example")
    assert evil.get("/api/history").status_code == 400
    assert evil.post("/api/settings", json={"provider": "offline"}).status_code == 400
    assert client.get("/api/history").status_code == 200  # 127.0.0.1 still fine
    assert TestClient(app, base_url="http://localhost:8765").get("/api/health").status_code == 200


def test_bad_ollama_host_is_rejected_not_persisted(client):
    r = client.post("/api/settings", json={"provider": "ollama", "ollama_host": "http://127.0.0.1:port"})
    assert r.status_code == 400 and "valid address" in r.json()["detail"]
    assert client.get("/api/status").status_code == 200  # not bricked
    r = client.post("/api/settings", json={"provider": "offline", "ollama_host": "127.0.0.1:11434"})
    assert r.json()["settings"]["ollama_host"] == "http://127.0.0.1:11434"  # scheme added


def test_reply_and_ask_accept_partial_result(client):
    r = client.post("/api/reply", json={"result": {"title": "x"}, "goal": "question"})
    assert r.status_code == 200 and "[Your name]" in r.json()["text"]
    r = client.post("/api/ask", json={"text": "Rent is due on the 1st.", "result": {}, "question": "rent"})
    assert r.status_code == 200


def test_env_keys_are_not_written_to_config(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env-0000000000")
    client.post("/api/settings", json={"save_history": True})
    from paperwork_pilot.config import config_path
    import json as _json

    assert "sk-from-env" not in config_path().read_text()
    assert _json.loads(config_path().read_text())["openai_api_key"] == ""


def test_clearing_a_key(client):
    client.post("/api/settings", json={"openai_api_key": "sk-realkey-1234567890"})
    client.post("/api/settings", json={"openai_api_key": ""})
    assert client.get("/api/status").json()["settings"]["has_openai_key"] is False


# ---------------- G6 privacy: zero network in offline mode ----------------
def test_offline_mode_makes_no_network_calls(client, monkeypatch):
    calls = []

    def guard(self, *a, **k):
        calls.append(a)
        raise AssertionError("Network call attempted in offline mode!")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)
    # Status checks detect_ollama() which legitimately probes localhost; verify analyze path separately.
    r = client.post("/api/analyze/sample/subscription_terms.txt")
    assert r.status_code == 200
    doc = r.json()
    client.post("/api/reply", json={"result": doc["result"], "goal": "cancel", "tone": "firm", "details": "", "kind": "letter"})
    client.post("/api/ask", json={"text": doc["text"], "result": doc["result"], "question": "refund"})
    assert calls == []


# ---------------- provider fallback with a fake AI ----------------
class FakeProvider(providers.BaseProvider):
    name = "ollama"
    local = True

    def __init__(self, settings, behavior):
        super().__init__(settings)
        self.behavior = behavior

    def check(self):
        return True, "fake"

    def complete(self, system_prompt, user_prompt, want_json=False):
        if self.behavior == "good":
            return '{"document_type":"lease","title":"Test Lease","plain_summary":"You are renting a flat.","deadlines":[{"date":"Sept 1, 2026","what":"Move in","urgency":"soon"}],"money":[],"watch_outs":[{"title":"Auto renew","why":"keeps going","quote":"renew","severity":"high"}],"your_options":["Negotiate"],"questions_to_ask":["Why?"],"next_steps":["Sign"],"glossary":[],"confidence":"high"}'
        if self.behavior == "prose":
            return "Here you go:\n```json\n{\"plain_summary\": \"Wrapped in prose.\", \"document_type\": \"other\"}\n```"
        if self.behavior == "garbage":
            return "I cannot help with that."
        raise ProviderError("Could not reach Ollama. Is it running?")


@pytest.mark.parametrize("behavior,expect_fallback", [("good", False), ("prose", False), ("garbage", True), ("error", True)])
def test_analyze_with_fake_ai(monkeypatch, samples_dir, behavior, expect_fallback):
    monkeypatch.setenv("PAPERWORK_PROVIDER", "ollama")
    s = load_settings()
    monkeypatch.setattr("paperwork_pilot.analysis.engine.get_provider", lambda st: FakeProvider(st, behavior))
    r = analyze((samples_dir / "lease.txt").read_text(), s)
    assert r["meta"]["fallback"] is expect_fallback
    assert r["plain_summary"]
    if behavior == "good":
        assert r["title"] == "Test Lease"
        # safety net added the rules-found amounts the AI omitted
        assert any("$1,450.00" in m["amount"] for m in r["money"])
    if expect_fallback:
        assert "problem" in r["notes"][0].lower() or "basic" in r["notes"][0].lower()


def test_reply_and_ask_fall_back_on_error(monkeypatch, samples_dir):
    monkeypatch.setenv("PAPERWORK_PROVIDER", "ollama")
    s = load_settings()
    monkeypatch.setattr("paperwork_pilot.analysis.engine.get_provider", lambda st: FakeProvider(st, "error"))
    from paperwork_pilot.analysis.rules import analyze_rules

    text = (samples_dir / "lease.txt").read_text()
    res = analyze_rules(text)
    rep = write_reply(res, "question", "polite", "", "email", s)
    assert rep["fallback"] and "[Your name]" in rep["text"]
    ans = ask_question(text, res, "deposit", s)
    assert "problem" in ans["answer"].lower()


def test_long_document_chunking(monkeypatch, samples_dir):
    monkeypatch.setenv("PAPERWORK_PROVIDER", "ollama")
    s = load_settings()
    calls = []

    class Counting(FakeProvider):
        def complete(self, system_prompt, user_prompt, want_json=False):
            calls.append(user_prompt[:30])
            return super().complete(system_prompt, user_prompt, want_json)

    monkeypatch.setattr("paperwork_pilot.analysis.engine.get_provider", lambda st: Counting(st, "good"))
    text = (samples_dir / "lease.txt").read_text() * 8  # ~30k chars -> multi-chunk path
    r = analyze(text, s)
    assert len(calls) >= 3  # chunks + merge
    assert r["meta"]["fallback"] is False
