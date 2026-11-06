# Architecture (for developers)

Paperwork Pilot is a small local-first web app: a FastAPI backend serving a single static page, with a pluggable LLM layer and a deterministic rules engine as a permanent safety net.

```
┌──────────────────────────────── Browser (127.0.0.1) ───────────────────────────────┐
│  static/index.html + app.js + app.css   (vanilla JS, no build step, no CDN)         │
└───────────────┬────────────────────────────────────────────────────────▲────────────┘
                │ JSON over HTTP                                           │
┌───────────────▼────────────────────────────────────────────────────────┴────────────┐
│  server.py (FastAPI, uvicorn)                                                         │
│   /api/analyze/{file,text,sample}  → extract.py → analysis.analyze_quick  (instant)  │
│   /api/enhance                     → analysis.analyze  (AI, threaded)                │
│   /api/reply  /api/ask  /api/settings  /api/test-connection  /api/history            │
└───────┬───────────────────────────────────┬───────────────────────────┬──────────────┘
        │                                   │                           │
┌───────▼────────┐              ┌───────────▼───────────┐     ┌─────────▼──────────┐
│ extract.py     │              │ analysis/              │     │ history.py (SQLite)│
│ pdf/docx/txt   │              │  rules.py   (no AI)    │     │ config.py  (JSON)  │
│ image/OCR via  │              │  engine.py  (orchestr.)│     │ ~/.paperwork_pilot │
│ system tools   │              │  prompts.py            │     └────────────────────┘
└────────────────┘              │  schema.py  (normalize)│
                                └───────────┬───────────┘
                                            │
                                ┌───────────▼───────────┐
                                │ llm/providers.py      │
                                │  Ollama  (local)      │
                                │  OpenAI-compatible    │
                                │  Gemini               │
                                │  Offline (no-op)      │
                                └───────────────────────┘
```

## Design decisions

**Two-phase results.** `analyze_quick()` (regex/keyword rules, ~10 ms) returns immediately so the UI is useful at once. If an AI provider is selected, the browser then calls `/api/enhance`, which runs the full LLM pass (1–3 min on CPU for a 3B model) and replaces the result in place. The user can ask questions or write a reply while waiting.

**Rules as a safety net, not just a fallback.** After the LLM result is normalized, `merge_safety_net()` adds any dates, amounts, watch-outs, or glossary terms the rules found but the model omitted. Small models drop things; regex doesn't.

**Normalize everything.** `schema.normalize()` coerces whatever the model returns (strings where lists are expected, missing keys, bad enums) into one fixed shape. The front-end never defends against malformed data.

**Friendly errors are data.** Providers raise `ProviderError` with a user-facing sentence. The orchestrator never lets an exception reach the UI as a 500; it degrades to the rules result and puts the message in `notes`.

**Long documents.** Above ~14k chars the engine chunks (~9k chars each, max 10), extracts per chunk, then runs a merge prompt. Lists are unioned so merge omissions don't lose data.

**No framework front-end.** ~500 lines of vanilla JS. Zero dependencies to break, zero build, works offline, trivially auditable.

**Local only.** Uvicorn binds `127.0.0.1`. There is no auth because there is no remote access.

## Provider interface

```python
class BaseProvider:
    def check(self) -> tuple[bool, str]            # (ok, human message)
    def complete(self, system: str, user: str, want_json: bool = False) -> str
```

Add a provider: subclass, register in `PROVIDERS`, add a radio button in `index.html` and a default model in `config.DEFAULT_MODELS`.

JSON mode is requested natively where supported (Ollama `format: json`, OpenAI `response_format`, Gemini `responseMimeType`) and `extract_json()` repairs fenced/prose-wrapped/trailing-comma output as a backstop.

## Data

- `~/.paperwork_pilot/config.json` — `Settings` dataclass, chmod 600. Env vars override (see `config.py` docstring).
- `~/.paperwork_pilot/history.db` — one table `documents(id, created, filename, title, doc_type, text, result_json)`.
- Override location with `PAPERWORK_DATA_DIR` (tests use a temp dir).

## Testing

```
tests/unit/     rules, normalizer, JSON repair, extraction, config     (fast, no I/O)
tests/smoke/    API via TestClient, fake-provider fallbacks, privacy (socket guard), two-phase
tests/ui/       Playwright: full click-through, console errors, text leaks, responsive, screenshots
scripts/        selfcheck.py (user diagnostics), readability_check.py, fresh_install_test.sh
```

Run everything: `pip install -r requirements-dev.txt && python -m playwright install chromium && pytest`.

## Extending

- **New document type:** add keywords to `rules.TYPE_KEYWORDS`, a label to `schema.DOC_TYPES`, and (optionally) default options/questions in `rules.analyze_rules`.
- **New watch-out pattern:** one tuple in `rules.WATCH_PATTERNS`.
- **New reply goal:** add to `engine.REPLY_GOALS` and a template sentence in `_template_reply`.
