"""UI regression: the two-phase flow with a (fake) cloud AI.

Starts a tiny OpenAI-compatible HTTP server that answers slowly for the big
analysis prompt and quickly for everything else, then checks in a real browser:
  * instant quick result + blue AI banner appear
  * a question asked WHILE the AI is working still gets its answer after the
    upgrade re-renders the page (regression for a detached-DOM bug)
  * after the upgrade, the AI title/summary are shown and the banner is gone
  * 'Skip the AI' hides the banner
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

pytestmark = pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")

ANALYSIS_JSON = {
    "document_type": "lease", "title": "Fake AI Lease Title",
    "plain_summary": "The fake AI says: you are renting an apartment and must pay on time.",
    "who_sent_it": "Maplewood", "what_they_want": "Sign it.",
    "key_facts": [{"label": "Rent", "value": "$1,450.00"}], "deadlines": [], "money": [],
    "watch_outs": [{"title": "Auto-renew", "why": "It renews.", "quote": "automatically renew", "severity": "high"}],
    "your_options": ["Negotiate"], "questions_to_ask": ["Why?"], "next_steps": ["Sign"], "glossary": [], "confidence": "high",
}


class FakeOpenAI(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        self._send({"data": [{"id": "fake-model"}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        user = body["messages"][-1]["content"]
        if "fill in this JSON" in user:          # the big analysis prompt
            time.sleep(4)
            content = json.dumps(ANALYSIS_JSON)
        elif "QUESTION:" in user:
            content = "FAKE ANSWER: the late fee is $75 plus $10 a day."
        else:
            content = "Dear Sir or Madam, this is a fake reply. [Your name]"
        self._send({"choices": [{"message": {"content": content}}]})

    def _send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def fake_ai():
    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), FakeOpenAI)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}/v1"
    httpd.shutdown()


@pytest.fixture(scope="module")
def server(tmp_path_factory, fake_ai):
    data = tmp_path_factory.mktemp("data")
    port = _free_port()
    env = {
        **os.environ,
        "PAPERWORK_DATA_DIR": str(data),
        "PAPERWORK_PROVIDER": "openai",
        "PAPERWORK_MODEL": "fake-model",
        "OPENAI_API_KEY": "sk-fake",
        "OPENAI_BASE_URL": fake_ai,
    }
    proc = subprocess.Popen([sys.executable, "app.py", "--port", str(port), "--no-browser"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _setup_done(page, server):
    page.goto(server)
    page.wait_for_selector("#screen-home:not([hidden]), #screen-setup:not([hidden])", timeout=10000)
    if page.is_visible("#screen-setup"):
        # pick cloud; the env already provides key+url so it is 'ready'
        page.click('.choice[data-choice="cloud"]')
        page.wait_for_selector("#screen-home:not([hidden])")
        page.keyboard.press("Escape")  # close the settings modal it opens


def test_ask_during_ai_upgrade(server):
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _setup_done(page, server)
        assert "Cloud" in page.inner_text("#engineText")

        page.click('.sample-btn[data-sample="lease.txt"]')
        page.wait_for_selector("#screen-results:not([hidden])", timeout=15000)
        assert page.is_visible("#aiBanner")
        assert "quick scan" in page.inner_text("#rMeta")

        # ask while the AI is still working
        page.click("#askToggle")
        page.fill("#askInput", "how much is the late fee?")
        page.click("#askBtn")

        # AI upgrade lands
        page.wait_for_selector("#aiBanner", state="hidden", timeout=30000)
        assert page.inner_text("#rTitle") == "Fake AI Lease Title"
        assert "fake AI says" in page.inner_text("#rSummary")
        assert "cloud AI" in page.inner_text("#rMeta")

        # ...and the answer still shows up in the live thread
        page.wait_for_selector("#askThread .qa .a:not(.muted)", timeout=15000)
        assert "FAKE ANSWER" in page.inner_text("#askThread")
        assert errors == [], errors
        b.close()


def test_skip_ai_hides_banner(server):
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        _setup_done(page, server)
        page.click('.sample-btn[data-sample="job_offer.txt"]')
        page.wait_for_selector("#aiBanner:not([hidden])", timeout=15000)
        page.click("#aiSkip")
        page.wait_for_selector("#aiBanner", state="hidden", timeout=5000)
        assert "quick scan" in page.inner_text("#rMeta")  # still the quick result
        b.close()
