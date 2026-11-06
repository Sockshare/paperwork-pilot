"""Two-phase flow: instant quick result, then /api/enhance upgrades it and updates history."""

from paperwork_pilot.llm import providers


class GoodFake(providers.BaseProvider):
    name = "ollama"
    local = True

    def check(self):
        return True, "fake"

    def complete(self, system_prompt, user_prompt, want_json=False):
        return '{"document_type":"lease","title":"AI Lease Title","plain_summary":"AI wrote this summary for you.","deadlines":[],"money":[],"watch_outs":[],"your_options":[],"questions_to_ask":[],"next_steps":[],"glossary":[],"confidence":"high"}'


def test_quick_then_enhance(client, monkeypatch):
    monkeypatch.setenv("PAPERWORK_PROVIDER", "ollama")
    monkeypatch.setattr("paperwork_pilot.analysis.engine.get_provider", lambda st: GoodFake(st))

    first = client.post("/api/analyze/sample/lease.txt").json()
    assert first["ai_pending"] is True
    assert first["result"]["meta"]["engine"] == "offline" and first["result"]["meta"]["quick"] is True
    assert first["result"]["money"]  # instant rules result is useful on its own
    assert not any("Basic scan" in n for n in first["result"]["notes"])  # no nag while AI is coming

    second = client.post("/api/enhance", json={"id": first["id"], "text": first["text"], "warnings": []}).json()
    r = second["result"]
    assert r["title"] == "AI Lease Title" and r["meta"]["engine"] == "ollama" and r["meta"]["fallback"] is False
    assert r["money"]  # safety net merged rules amounts back in

    saved = client.get(f"/api/history/{first['id']}").json()
    assert saved["result"]["title"] == "AI Lease Title"


def test_offline_has_no_pending(client):
    first = client.post("/api/analyze/sample/lease.txt").json()
    assert first["ai_pending"] is False
    assert any("Basic scan" in n for n in first["result"]["notes"])
