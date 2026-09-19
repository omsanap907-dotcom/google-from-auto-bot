import os
import time
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, request, send_from_directory
from playwright.sync_api import sync_playwright

APP_DIR = Path(__file__).resolve().parent
app = Flask(__name__)
_lock = Lock()
_state = {"status": "Idle", "message": "", "processed": 0, "total": 0}
TARGET_URL = "https://www.plagiarismremover.co/"

def set_state(**kwargs):
    with _lock:
        _state.update(kwargs)

def first_visible(page, selectors):
    for selector in selectors:
        loc = page.locator(selector)
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    return el
            except Exception:
                pass
    return None

def value_of(el):
    try:
        return el.input_value()
    except Exception:
        try:
            return el.inner_text()
        except Exception:
            return ""

def click_button(page):
    selectors = ["button", "[role='button']", "input[type='submit']", "input[type='button']"]
    needles = ("remove plagiarism", "plagiarism remover", "remove")
    for selector in selectors:
        loc = page.locator(selector)
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                text = " ".join([
                    el.inner_text() or "",
                    el.get_attribute("value") or "",
                    el.get_attribute("aria-label") or "",
                    el.get_attribute("title") or "",
                ]).casefold()
                if any(n in text for n in needles):
                    return el
            except Exception:
                pass
    return None

def find_output(page, original):
    selectors = [
        "textarea", "[contenteditable='true']",
        "[id*='result' i]", "[class*='result' i]",
        "[id*='output' i]", "[class*='output' i]"
    ]
    locs = []
    for selector in selectors:
        loc = page.locator(selector)
        for i in range(loc.count()):
            locs.append(loc.nth(i))
    for _ in range(60):
        page.wait_for_timeout(1000)
        for el in locs:
            try:
                if not el.is_visible():
                    continue
                v = value_of(el).strip()
                if v and v != original.strip():
                    return v
            except Exception:
                pass
    return ""

@app.post("/api/inspect")
def inspect():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            inp = first_visible(page, ["textarea", "[contenteditable='true']", "input[type='text']"])
            btn = click_button(page)
            return jsonify({
                "url": page.url,
                "title": page.title(),
                "inputDetected": bool(inp),
                "removeButtonDetected": bool(btn)
            })
        finally:
            browser.close()

@app.post("/api/process")
def process():
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", ""))
    if not text.strip():
        return jsonify({"error": "Text is required."}), 400
    if len(text.split()) > 500:
        return jsonify({"error": "Maximum 500 words per request."}), 400

    set_state(status="Running", message="Opening target site.", processed=0, total=1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            inp = first_visible(page, ["textarea", "[contenteditable='true']", "input[type='text']"])
            if not inp:
                raise RuntimeError("Could not find the site's text input.")

            inp.fill(text)
            page.wait_for_timeout(300)

            btn = click_button(page)
            if not btn:
                raise RuntimeError("Could not find the site's processing button.")

            btn.click(force=True)
            output = find_output(page, text)
            if not output:
                raise RuntimeError("No changed output was detected within 60 seconds.")

            set_state(status="Completed", message="Processing completed.", processed=1, total=1)
            return jsonify({"output": output, "words": len(output.split())})
        except Exception as exc:
            set_state(status="Error", message=str(exc))
            return jsonify({"error": str(exc)}), 500
        finally:
            browser.close()

@app.get("/api/status")
def status():
    with _lock:
        return jsonify(dict(_state))

@app.get("/")
def index():
    return send_from_directory(APP_DIR, "index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
