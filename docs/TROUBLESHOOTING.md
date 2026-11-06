# Troubleshooting

**First, run the checker.** It finds most problems and tells you the fix:

```bash
source .venv/bin/activate
python scripts/selfcheck.py
```

---

## Starting the app

| What you see | Why | Fix |
|---|---|---|
| `python3: command not found` | Python isn't installed | `sudo apt install python3 python3-venv` |
| `The virtual environment was not created successfully because ensurepip is not available` | Debian/Ubuntu ships Python without venv | `sudo apt install python3-venv` then redo step 2 of the README |
| `Some required packages are not installed yet` | You skipped `pip install` or forgot to activate the venv | `source .venv/bin/activate` then `pip install -r requirements.txt` |
| `Port 8765 is busy, using 8766 instead` | Something else uses that port | Nothing to fix — use the address the app prints |
| Browser didn't open | Some Linux setups block auto-open | Open a browser yourself and go to `http://127.0.0.1:8765` |
| "The app's server isn't answering. Go back to the terminal…" | The terminal running `python app.py` was closed, or the computer went to sleep | Start it again with `python app.py` and reload the page |
| "The app couldn't load its settings" | The settings file is unreadable | Run `python scripts/selfcheck.py`; if needed delete `~/.paperwork_pilot/config.json` (you will see the welcome screen again) |

---

## Reading files

| What you see | Why | Fix |
|---|---|---|
| "This PDF seems to be a scanned picture with no text layer" | The PDF is just images | Install OCR: `sudo apt install tesseract-ocr poppler-utils`, then try again. Or paste the text by hand. |
| "Reading photos needs the free Tesseract OCR tool" | Tesseract isn't installed | `sudo apt install tesseract-ocr` |
| "The photo was too blurry or dark to read" | OCR couldn't make out the letters | Retake it: flat, straight-on, bright, fill the frame. Or paste the text. |
| "This PDF is password protected" | The file is locked | Open it in a PDF viewer, print/save as a new PDF without the password |
| "'.doc' files are not supported" | Old Word format | Open in Word/LibreOffice and save as `.docx` |
| "That file is bigger than 25 MB" | Very large scan | Export only the pages you need, or lower the scan resolution |
| "There isn't enough text to analyze" / "Paste a bit more text first" | Fewer than 20 characters came out | Paste more of the document |
| "The file is empty" | The file has zero bytes | Re-download or re-save the file |
| "This file doesn't look like a real PDF" | The file is damaged or was renamed to .pdf | Open it in a PDF viewer and save it again |
| "This doesn't look like a valid Word file" | Damaged .docx | Open in Word/LibreOffice and save as .docx again |
| "This Word file is too large to open safely" | Extremely large .docx | Copy the text and paste it instead |
| Numbers look wrong after OCR | OCR misread digits | Always compare with the original; the "Show the original text" link helps |

---

## The AI

| What you see | Why | Fix |
|---|---|---|
| "Ollama isn't running (or isn't installed)" | Ollama isn't installed, or isn't started | Install: `curl -fsSL https://ollama.com/install.sh \| sh` then `ollama pull llama3.2:3b`. If installed, run `ollama serve` in another terminal |
| "'…' is not a valid address" | A typo in the Ollama address | Use `http://127.0.0.1:11434` unless you know you changed it |
| "Model 'llama3.2:3b' is not downloaded" | The model hasn't been pulled | `ollama pull llama3.2:3b` |
| "Ollama took too long to answer" | Document is long or the computer is slow | Try a smaller model (`ollama pull llama3.2:1b` and pick it in Settings), or use the quick scan (click **Skip the AI**) |
| AI takes 4+ minutes | Normal on older or busy CPUs with the 3B model | Use `llama3.2:1b`, or a cloud key, or just use the instant quick scan — it already has dates, money, and watch-outs |
| "The API key was rejected" | Typo or expired key | Paste it again in Settings and click **Test** |
| "out of credits or sending too fast" | Your account's limit | Wait a minute, or add credit to your account |
| "Could not reach the API server" | No internet, or wrong server address | Check your connection; for OpenAI the address should be `https://api.openai.com/v1` |
| Yellow box: "The AI had a problem… Showing the basic scan instead" | The AI failed, so you got the rules-based result | Read the message in the box — it says what went wrong. The basic scan is still useful. |
| "The AI step didn't finish" | The AI call failed after the quick scan | Same as above; check Settings → Test |
| "Type a question first" | The question box was empty | Type a question, then click Ask |
| Answer looks incomplete or odd | Small local models sometimes misread | Click **Start over** and try again, or try a bigger model (`qwen2.5:7b`, `llama3.1:8b`) if your computer has 8+ GB RAM |

### Which Ollama model should I use?

| Model | RAM needed | Speed on CPU | Quality |
|---|---|---|---|
| `llama3.2:1b` | ~2 GB | Fast | OK for short letters |
| `llama3.2:3b` (default) | ~4 GB | Medium | Good |
| `qwen2.5:3b` | ~4 GB | Medium | Good, strong at structure |
| `qwen2.5:7b` / `llama3.1:8b` | ~8 GB | Slow on CPU | Best |

Download with `ollama pull <name>`, then pick it in **Settings → Model**.

---

## Settings and history

| What you see | Why | Fix |
|---|---|---|
| Settings don't stick | The config file couldn't be written | Check that `~/.paperwork_pilot/` exists and you own it |
| History is empty | "Remember documents" is off | Turn it on in Settings |
| I want to wipe everything | | Settings → **Delete all saved documents**, or delete the folder `~/.paperwork_pilot/` |
| I want to move the data folder | | Start with `PAPERWORK_DATA_DIR=/some/folder python app.py` |

---

## Still stuck?

1. Run `python app.py --verbose` and look at the messages in the terminal.
2. Open an issue on GitHub and paste the output of `python scripts/selfcheck.py` (it contains no document text or keys).
