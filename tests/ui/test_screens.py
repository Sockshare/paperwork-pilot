"""G4 — UI render test (Playwright).

Boots the real server on a free port in OFFLINE mode, then drives a real
Chromium browser through: first-run screen -> home -> example -> results ->
reply modal -> ask -> settings -> history. Asserts:

  * zero JavaScript console errors / page errors / failed requests
  * no "undefined", "[object Object]", "NaN" or stray "null" in visible text
  * every major element is visible
  * layout works at desktop, laptop and phone widths
  * screenshots are written to docs/screenshots/ for the README

Skipped automatically if Playwright's browser is not installed:
    pip install -r requirements-dev.txt && python -m playwright install chromium
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SHOTS = ROOT / "docs" / "screenshots"

try:
    from playwright.sync_api import sync_playwright  # noqa: E402
except ImportError:  # pragma: no cover
    sync_playwright = None

pytestmark = pytest.mark.skipif(sync_playwright is None, reason="playwright not installed")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    port = _free_port()
    env = {**os.environ, "PAPERWORK_DATA_DIR": str(data), "PAPERWORK_PROVIDER": "offline"}
    proc = subprocess.Popen(
        [sys.executable, "app.py", "--port", str(port), "--no-browser"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
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


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Chromium not available for Playwright: {exc}")
        yield b
        b.close()


def _collect(page):
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url} {r.failure}"))
    page.on("response", lambda r: errors.append(f"http {r.status}: {r.url}") if r.status >= 400 else None)
    return errors


def _no_leaks(page):
    body = page.inner_text("body")
    for bad in ("undefined", "[object Object]", "NaN"):
        assert bad not in body, f"'{bad}' leaked into the page"
    import re

    assert not re.search(r"\bnull\b", body), "'null' leaked into the page"


def test_full_flow(server, browser):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    errors = _collect(page)
    page.goto(server)

    # --- first run ---
    page.wait_for_selector("#screen-setup:not([hidden])", timeout=10000)
    assert page.locator(".choice").count() == 3
    page.screenshot(path=SHOTS / "01_first_run.png", full_page=True)
    page.click('.choice[data-choice="offline"]')

    # --- home ---
    page.wait_for_selector("#screen-home:not([hidden])", timeout=10000)
    assert page.is_visible("#dropzone")
    assert page.locator(".sample-btn").count() >= 5
    assert "never leave this computer" in page.inner_text("#privacyText")
    page.screenshot(path=SHOTS / "02_home.png", full_page=True)

    # --- paste too-short text gives a friendly message, not a crash ---
    page.fill("#pasteText", "hi")
    page.click("#pasteBtn")
    page.wait_for_selector("#toast:not([hidden])")
    assert "more text" in page.inner_text("#toast")

    # --- run an example ---
    page.click('.sample-btn[data-sample="lease.txt"]')
    page.wait_for_selector("#screen-results:not([hidden])", timeout=30000)
    assert len(page.inner_text("#rSummary")) > 40
    assert page.locator("#rWatch .watch").count() >= 1
    assert page.locator("#rDeadlines .dl").count() >= 1
    assert page.locator("#rMoney .money-row").count() >= 1
    assert page.locator("#rGlossary dt").count() >= 1
    _no_leaks(page)
    page.screenshot(path=SHOTS / "03_results.png", full_page=True)

    # --- reply modal ---
    page.click("#replyBtn")
    page.wait_for_selector("#modal-reply:not([hidden])")
    assert page.locator("#replyGoal option").count() >= 5
    page.fill("#replyDetails", "I already paid $200 on October 3.")
    page.click("#replyGo")
    page.wait_for_selector("#replyOut:not([hidden])", timeout=30000)
    reply = page.input_value("#replyText")
    assert len(reply) > 100 and "October 3" in reply
    page.screenshot(path=SHOTS / "04_reply.png")
    page.keyboard.press("Escape")
    page.wait_for_selector("#modal-reply", state="hidden")

    # --- ask ---
    page.click("#askToggle")
    page.fill("#askInput", "late fee")
    page.click("#askBtn")
    page.wait_for_selector("#askThread .qa .a:not(.muted)", timeout=30000)
    assert "late fee" in page.inner_text("#askThread").lower()
    _no_leaks(page)

    # --- settings ---
    page.click("#settingsBtn")
    page.wait_for_selector("#modal-settings:not([hidden])")
    assert page.is_checked('input[name=prov][value=offline]')
    page.click('input[name=prov][value=gemini]')
    assert page.is_visible('.prov-panel[data-prov="gemini"]')
    page.click("#setTest")
    page.wait_for_function("document.getElementById('setTestOut').textContent.includes('✗')")
    assert "key" in page.inner_text("#setTestOut").lower()
    page.screenshot(path=SHOTS / "05_settings.png")
    page.click('input[name=prov][value=offline]')
    page.click("#setSave")
    page.wait_for_selector("#modal-settings", state="hidden")

    # --- history ---
    page.click("#historyBtn")
    page.wait_for_selector("#modal-history:not([hidden])")
    assert page.locator(".hist-item").count() >= 1
    page.keyboard.press("Escape")

    # --- back home shows recent ---
    page.click("#backBtn")
    page.wait_for_selector("#screen-home:not([hidden])")
    assert page.is_visible("#recentBox")

    assert errors == [], errors
    page.close()


@pytest.mark.parametrize("width,name", [(1024, "laptop"), (420, "phone")])
def test_responsive_results(server, browser, width, name):
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = _collect(page)
    page.goto(server)
    page.wait_for_selector("#screen-home:not([hidden]), #screen-setup:not([hidden])", timeout=10000)
    if page.is_visible("#screen-setup"):
        page.click('.choice[data-choice="offline"]')
        page.wait_for_selector("#screen-home:not([hidden])")
    page.click('.sample-btn[data-sample="medical_bill.txt"]')
    page.wait_for_selector("#screen-results:not([hidden])", timeout=30000)
    # nothing should overflow horizontally
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 2, f"horizontal overflow of {overflow}px at {width}px"
    assert page.is_visible("#replyBtn")
    page.screenshot(path=SHOTS / f"06_results_{name}.png", full_page=True)
    assert errors == [], errors
    page.close()
