"""FastAPI server. Serves the one-page UI and a small JSON API.

Binds to 127.0.0.1 only — the app is never reachable from other computers.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, history
from .analysis import REPLY_GOALS, REPLY_TONES, analyze, analyze_quick, ask_question, normalize, strip_basic_note, write_reply
from .config import SettingsError, load_settings, update_settings, validate_changes
from .extract import SUPPORTED_EXTS, extract_text
from .llm import detect_ollama, get_provider

log = logging.getLogger("paperwork_pilot")

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
SAMPLES = ROOT.parent / "samples"

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_TEXT_CHARS = 120_000

app = FastAPI(title="Paperwork Pilot", version=__version__, docs_url=None, redoc_url=None)
# Only accept requests addressed to this machine. Stops "DNS rebinding" web pages
# from talking to the API through the user's browser.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _provider_status(s) -> dict:
    provider = get_provider(s)
    try:
        ok, message = provider.check()
    except Exception as exc:  # noqa: BLE001 - a check must never take the UI down
        log.warning("provider check failed: %s", exc)
        ok, message = False, "Could not check the AI engine. Open Settings and click Test."
    return {"name": provider.name, "model": provider.model, "local": provider.local, "ok": ok, "message": message}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ---------------------------------------------------------------------------
# Status + settings
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/api/status")
def status():
    s = load_settings()
    ollama = detect_ollama(s.ollama_host)
    if s.provider == "ollama":
        # Reuse the probe instead of hitting Ollama again.
        provider = get_provider(s)
        if ollama.get("error"):
            pstat = {"ok": False, "message": ollama["error"]}
        elif not ollama["running"]:
            pstat = {"ok": False, "message": "Ollama isn't running (or isn't installed). See Settings for the install steps, or start it with: ollama serve"}
        elif not ollama["models"]:
            pstat = {"ok": False, "message": f"Ollama is running but has no models. Run: ollama pull {provider.model}"}
        elif not any(m == provider.model or m.split(":")[0] == provider.model.split(":")[0] for m in ollama["models"]):
            pstat = {"ok": False, "message": f"Model '{provider.model}' is not downloaded. Run: ollama pull {provider.model}"}
        else:
            pstat = {"ok": True, "message": f"Ollama is ready with {provider.model}"}
        pstat.update({"name": provider.name, "model": provider.model, "local": True})
    else:
        pstat = _provider_status(s)
    return {
        "settings": s.public_dict(),
        "provider": pstat,
        "ollama": ollama,
        "supported_files": sorted(SUPPORTED_EXTS),
        "reply_goals": REPLY_GOALS,
        "reply_tones": REPLY_TONES,
    }


class SettingsIn(BaseModel):
    provider: str | None = None
    model: str | None = None
    ollama_host: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    gemini_api_key: str | None = None
    save_history: bool | None = None
    setup_complete: bool | None = None


@app.post("/api/settings")
def set_settings(body: SettingsIn):
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        s = update_settings(changes)
    except SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"settings": s.public_dict(), "provider": _provider_status(s)}


@app.post("/api/test-connection")
def test_connection(body: SettingsIn):
    """Try settings WITHOUT saving them. Used by the 'Test' button."""
    s = load_settings()
    try:
        changes = validate_changes({k: v for k, v in body.model_dump().items() if v is not None})
    except SettingsError as exc:
        return {"ok": False, "message": str(exc), "model": ""}
    for k, v in changes.items():
        if hasattr(s, k):
            if k in ("openai_api_key", "gemini_api_key") and "*" in str(v):
                continue
            setattr(s, k, v)
    st = _provider_status(s)
    return {"ok": st["ok"], "message": st["message"], "model": st["model"]}


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------
SAMPLE_META = {
    "lease.txt": {"title": "Apartment lease", "emoji": "🏠"},
    "medical_bill.txt": {"title": "Medical bill", "emoji": "🏥"},
    "insurance_denial.txt": {"title": "Insurance denial letter", "emoji": "📄"},
    "job_offer.txt": {"title": "Job offer", "emoji": "💼"},
    "debt_collection.txt": {"title": "Debt collection letter", "emoji": "✉️"},
    "subscription_terms.txt": {"title": "Subscription terms", "emoji": "📱"},
}


@app.get("/api/samples")
def samples():
    out = []
    for name, meta in SAMPLE_META.items():
        if (SAMPLES / name).exists():
            out.append({"name": name, **meta})
    return out


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------
def _analyze_text(filename: str, text: str, warnings: list[str]) -> dict:
    """Phase 1: instant rules-based result. If an AI engine is selected, the browser
    then calls /api/enhance to upgrade it (that can take a minute on a CPU)."""
    text = text.strip()
    if len(text) < 20:
        raise HTTPException(400, "There isn't enough text to analyze. Paste or upload more of the document.")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        warnings = warnings + ["The text was very long, so only the first part was analyzed."]
    s = load_settings()
    result = analyze_quick(text)
    ai_pending = s.provider != "offline"
    if ai_pending:
        result = strip_basic_note(result)  # no "turn on AI" nag — the AI is coming
    if warnings:
        result["notes"] = warnings + result.get("notes", [])
    doc_id = history.save(filename, text, result) if s.save_history else ""
    return {"id": doc_id, "filename": filename, "text": text, "chars": len(text), "result": result, "ai_pending": ai_pending, "warnings": warnings}


class EnhanceIn(BaseModel):
    id: str = ""
    text: str
    warnings: list[str] = []


def _enhance(body: EnhanceIn) -> dict:
    s = load_settings()
    text = body.text[:MAX_TEXT_CHARS]
    result = analyze(text, s)
    if body.warnings:
        result["notes"] = body.warnings + result.get("notes", [])
    if body.id and s.save_history:
        history.update(body.id, result)
    return {"result": result}


@app.post("/api/enhance")
async def enhance(body: EnhanceIn):
    """Phase 2: full AI analysis. Falls back to the rules result (with a note) if the AI fails."""
    return await asyncio.to_thread(_enhance, body)


@app.post("/api/analyze/file")
async def analyze_file(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "That file is bigger than 25 MB. Try a smaller file or just the pages you need.")
    # PDF parsing and OCR can take a while; keep the server responsive.
    ext = await asyncio.to_thread(extract_text, file.filename or "upload", data)
    if not ext.ok:
        raise HTTPException(400, ext.error or "The file had no readable text.")
    return await asyncio.to_thread(_analyze_text, file.filename or "upload", ext.text, ext.warnings)


class TextIn(BaseModel):
    text: str
    name: str = "Pasted text"


@app.post("/api/analyze/text")
async def analyze_pasted(body: TextIn):
    return await asyncio.to_thread(_analyze_text, body.name or "Pasted text", body.text, [])


@app.post("/api/analyze/sample/{name}")
async def analyze_sample(name: str):
    if name not in SAMPLE_META or not (SAMPLES / name).exists():
        raise HTTPException(404, "Sample not found.")
    text = (SAMPLES / name).read_text(encoding="utf-8")
    return await asyncio.to_thread(_analyze_text, SAMPLE_META[name]["title"] + " (example)", text, [])


# ---------------------------------------------------------------------------
# Reply + Ask
# ---------------------------------------------------------------------------
class ReplyIn(BaseModel):
    result: dict
    goal: str = "question"
    tone: str = "polite"
    details: str = ""
    kind: str = "email"


@app.post("/api/reply")
async def reply(body: ReplyIn):
    s = load_settings()
    return await asyncio.to_thread(write_reply, normalize(body.result), body.goal, body.tone, body.details[:3000], body.kind, s)


class AskIn(BaseModel):
    text: str
    result: dict
    question: str


@app.post("/api/ask")
async def ask(body: AskIn):
    s = load_settings()
    return await asyncio.to_thread(ask_question, body.text[:MAX_TEXT_CHARS], normalize(body.result), body.question[:1000], s)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
@app.get("/api/history")
def history_list():
    return history.list_recent()


@app.get("/api/history/{doc_id}")
def history_get(doc_id: str):
    doc = history.get(doc_id)
    if not doc:
        raise HTTPException(404, "That document is no longer saved.")
    return doc


@app.delete("/api/history/{doc_id}")
def history_delete(doc_id: str):
    return {"deleted": history.delete(doc_id)}


@app.delete("/api/history")
def history_delete_all():
    return {"deleted": history.delete_all()}


# ---------------------------------------------------------------------------
# Friendly errors
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def unhandled(_, exc: Exception):
    log.exception("Unhandled error")
    return JSONResponse(status_code=500, content={"detail": f"Something went wrong inside the app ({type(exc).__name__}). Try again. If it keeps happening, run: python scripts/selfcheck.py"})
