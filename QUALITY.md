# Quality Report

Every release of Paperwork Pilot must pass the nine gates below before it is pushed.
This file records the results for the current release.

**Release:** 1.0.0  ·  **Platform tested:** Debian/Ubuntu-family Linux, Python 3.13, 8-core CPU, no GPU

## The nine gates

| # | Gate | What it proves | How to run it | Result |
|---|------|----------------|---------------|--------|
| G1 | Unit tests | Rules engine, normalizer, JSON repair, file extraction edge cases (encrypted PDF, zip-bomb DOCX, odd encodings), settings | `pytest tests/unit` | ✅ pass |
| G2 | API smoke tests | Server boots; every endpoint answers; all 6 samples analyze; two-phase quick→AI flow; history lifecycle; friendly errors on bad input | `pytest tests/smoke` | ✅ pass |
| G3 | Fresh install | README's 3 commands work in a brand-new venv in a temp folder, app starts, UI loads, a sample analyzes | `bash scripts/fresh_install_test.sh` | ✅ pass |
| G4 | UI in a real browser | Playwright clicks through first-run → home → example → results → reply → ask → settings → history at desktop/laptop/phone widths. Zero console errors, no `undefined`/`null` leaks, no horizontal overflow. Also: ask-a-question *while* the AI is working still gets answered (regression test for a real bug found in G9) | `pytest tests/ui` | ✅ pass |
| G5 | Readability | README, User Guide, Troubleshooting, FAQ, Privacy all read at grade 7 or below (Flesch-Kincaid) | `python scripts/readability_check.py` | ✅ pass (worst 6.1) |
| G6 | Privacy | With every network socket blocked, a full analyze + reply + ask in basic mode succeeds — proving nothing leaves the machine. Plus: foreign `Host` headers rejected (DNS-rebinding guard); env-provided keys never written to disk; Gemini key sent in a header, never a URL | `pytest tests/smoke -k "privacy or host or env_keys"` | ✅ pass |
| G7 | Independent novice-user review | A separate reviewer agent, following only the README, installed and used the app as a non-technical person and filed findings | report in review process | ✅ 0 P0 · 8 P1 (all fixed) · 16 P2 (14 fixed, 2 accepted) |
| G8 | Independent code & security review | A separate reviewer agent read every file, wrote 15 repro tests for suspected bugs, and ran them | report in review process | ✅ 2 P0 · 9 P1 (all fixed) · 14 P2 (12 fixed, 2 accepted) |
| G9 | Real local-AI run | Full flow in a real browser with a real `llama3.2:3b` model on CPU via Ollama: quick result, AI upgrade, question during upgrade, AI-written reply | manual, recorded below | ✅ pass |

## What the reviews found and what changed

**Security / privacy (from G8):**
- Gemini API key was passed as a URL parameter and could appear in request logs → now sent as a header; httpx request logging silenced at normal verbosity.
- `history.db` (document text) was created world-readable → now `600`, data folder `700`.
- No `Host` header check (DNS-rebinding risk) → `TrustedHostMiddleware` allows only `127.0.0.1` / `localhost`.
- `.docx` "zip bomb" could inflate to gigabytes → uncompressed size checked before parsing; text accumulation capped.
- OCR up-scaling had no pixel cap → 30 MP cap with down-scaling.
- Environment-provided API keys were persisted to `config.json` on any save → env values are never written back.

**Correctness (from G8 and G9):**
- Ollama dying mid-request produced an HTTP 500 → every transport error now becomes a plain-language message with the basic scan as fallback.
- A typo in the Ollama address could brick the UI → validated before saving; status endpoint can no longer crash; boot still renders Home if status fails.
- Password-protected PDFs were reported as "scanned picture" → correct message.
- Latin-1 / Windows text files were decoded as UTF-16 garbage → UTF-16 only with a byte-order mark; cp1252 tried first.
- A question asked while the AI was still working never showed its answer (found in G9) → fixed, with a browser regression test.
- Client-supplied result dicts are normalized server-side; normalizer can no longer raise.
- "Sept" / "Nov." dates now parse for urgency.

**Usability (from G7):**
- "Failed to fetch" replaced with "The app's server isn't answering… start it again with: python app.py".
- While the AI runs or after it fails, the summary no longer tells you to "turn on AI"; header says "quick scan (AI coming)" / "basic scan (the AI had a problem)".
- Basic-mode Money card now shows the line each amount sits on (e.g. "CT Scan … — $2,310.00") instead of the same paragraph eight times.
- Basic mode now extracts key facts (account/claim numbers, amount due, due date, salary, rent) and the template reply includes them.
- Consistent wording: "No AI (basic mode)", "AI option"; "EOB" spelled out.
- Phone layout: tighter header, wrapping for long words/URLs, stacked date rows.
- Toasts clear on navigation; stale "Test" results clear when switching AI option; "Remove saved key" buttons; OpenAI key hint; corrupt-PDF message; `--help` descriptions; no-sudo venv path in README; Troubleshooting covers every message the app can show.

**Accepted (documented, not changed):**
- Clicking **Skip the AI** stops waiting in the browser; the local model still finishes the current request in the background (bounded by a 15-minute total budget and 6 chunks). Next analysis may queue briefly behind it.
- A legitimate 30 MB Word document is still fully parsed (under the 60 MB safety cap) before truncation to 120k characters.
- `pypdf` is pinned to 5.1.0; run `pip-audit` before relying on untrusted PDFs at scale.

## G9 record — real local model

- Model: `llama3.2:3b` via Ollama on an 8-core CPU (no GPU), machine under test load.
- Sample: insurance denial letter (2.7k characters).
- Quick scan visible: **< 1 s**. AI upgrade: **~2–4 min** on this busy CPU (typical idle laptop: faster). Reply: ~1–2 min.
- Output checked by hand: document type, sender, 180-day appeal deadline, denial reason, appeal options and a usable appeal letter were all correct. One known limitation: the 3B model sometimes rates an arbitration clause "worth knowing" rather than "important"; the rules engine's own rating is merged in alongside it.

## Re-running everything

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt && python -m playwright install chromium
pytest                                   # G1, G2, G4, G6
bash scripts/fresh_install_test.sh       # G3
python scripts/readability_check.py      # G5
```
