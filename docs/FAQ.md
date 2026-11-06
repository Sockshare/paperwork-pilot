# FAQ

**Is this free?**
Yes. The app is free and open source (MIT). "On my computer" and "No AI" modes cost nothing. Cloud keys are billed by that company, usually a few cents per document.

**Do I need a powerful computer or a graphics card?**
No. The app itself is tiny. The free local AI (Ollama with `llama3.2:3b`) runs on an ordinary laptop CPU with 4 GB of free RAM. It's slower than a cloud AI (about 1–4 minutes per document) but completely private. The quick scan — dates, money, and watch-outs — is instant no matter what.

**Is it private?**
In "On my computer" and "No AI" modes, nothing leaves your PC. See [PRIVACY.md](PRIVACY.md).

**Can I trust what it says?**
Treat it like a smart friend, not a lawyer. It's good at finding dates, amounts, and common traps, and at explaining jargon. It can still miss things or misread a scan. Always check the important parts against the original document. For big decisions, talk to a professional — many areas have free legal aid.

**What kinds of documents work best?**
Leases, medical bills, insurance letters (denials, EOBs), job offers, debt collection letters, subscription terms, utility bills, government letters, and most contracts. Anything in English with real sentences.

**Does it work on Windows or Mac?**
It's built and tested on Linux. It should also run on macOS and Windows with the same three commands (use `.venv\Scripts\activate` on Windows), but those aren't tested.

**Can I use it in another language?**
The interface is English. With a cloud AI or a multilingual local model (e.g. `qwen2.5:3b`), documents in other languages often work, but quality varies.

**Can it read a photo of a letter?**
Yes, if you install the free Tesseract tool: `sudo apt install tesseract-ocr poppler-utils`. Take the photo flat, straight-on, in good light.

**Why does the AI take a few minutes?**
Local AI runs entirely on your CPU. That's the trade-off for privacy and zero cost. You get the instant quick scan right away and can keep using the page (ask questions, write a reply) while the AI finishes. A cloud key answers in seconds if you prefer speed.

**Can I use a different local AI, like LM Studio?**
Yes. In Settings pick **OpenAI (or compatible)** and set the server address to your local server (e.g. `http://127.0.0.1:1234/v1`). Local servers count as private.

**Where is my data stored?**
In `~/.paperwork_pilot/` — one settings file and one small history database. Delete the folder to remove everything.

**How do I update the app?**
`git pull` in the project folder, then `pip install -r requirements.txt` in case anything changed.

**How do I uninstall?**
Delete the project folder and `~/.paperwork_pilot/`. That's everything.

**Can I run it on a different port?**
`python app.py --port 9000`

**Can I stop the browser from opening automatically?**
`python app.py --no-browser`

**I'm a developer — how is it built?**
See [ARCHITECTURE.md](ARCHITECTURE.md) and run `pytest`.
