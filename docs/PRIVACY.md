# Privacy

Short version: **in "On my computer" and "No AI" modes, nothing leaves your computer. Ever.**

## What goes where

| Mode | Your document text | Your questions | Saved history | Anything sent to the internet? |
|---|---|---|---|---|
| 🔒 No AI (basic) | Stays on your PC | Stays on your PC | Local file only | **No** |
| 💻 On my computer (Ollama) | Sent to Ollama *on your own PC* (`127.0.0.1`) | Same | Local file only | **No** |
| ☁️ OpenAI / compatible | Sent to the server address in Settings | Same | Local file only | **Yes** — to that company |
| ☁️ Gemini | Sent to Google | Same | Local file only | **Yes** — to Google |

The pill in the top-right corner shows which mode is on. So does the privacy line at the bottom of the home screen.

## What the app stores on your computer

All in one folder: `~/.paperwork_pilot/`

- `config.json` — your settings. If you typed in an API key, it is here too. Only your user account can read this file.
- `history.db` — the documents you analyzed and their results. This is saved **only if** "Remember documents" is on. It is on by default. You can turn it off in Settings. You can also click **Delete all saved documents**.

Delete the folder and everything is gone.

## What the app never does

- It never contacts any server other than the AI you picked.
- It has no tracking. No analytics. No crash reports. It never "phones home."
- It listens only on `127.0.0.1`. Other computers on your network cannot reach it.
- It never uploads files anywhere; files are read in memory on your PC.

## How we check this

One of our automated tests blocks every network connection. Then it runs a full analysis, a reply, and a question in offline mode. If the app tried to reach the internet, that test would fail. See `tests/smoke/test_api.py::test_offline_mode_makes_no_network_calls`.

## Cloud mode: what the AI company sees

If you choose OpenAI or Gemini, your document text and your questions are sent to them. That is how they make the answer. Read their privacy policy. Do not use cloud mode for documents you are not allowed to share. "On my computer" mode is always there as a private choice.

## Keys

API keys live only in `config.json` on your computer. The browser never shows them in full (you see `sk-a*****1234`). They are never written to logs.
