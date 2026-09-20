import os
import re
import uuid
from pathlib import Path
from threading import Lock, Thread
from flask import Flask, jsonify, request, send_from_directory, send_file
from pypdf import PdfReader
from docx import Document
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

APP_DIR = Path(__file__).resolve().parent
WORK_DIR = APP_DIR / "work"
WORK_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
_lock = Lock()
_jobs = {}

# Allow the GitHub Pages frontend to call this Railway API.
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

TARGET_URL = "https://exampdfx.com/paraphrasing-tool"
MAX_CHARS = 2800
MAX_LOGS = 200
REQUEST_TIMEOUT_MS = 30000
RESULT_TIMEOUT_SECONDS = 60


def wc(s):
    return len(re.findall(r"\S+", s or ""))


def split_chunks(text, max_chars=MAX_CHARS):
    """Split into chunks that stay safely below ExampdfX's 3000-char input limit."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []

    words = text.split(" ")
    chunks = []
    current = []

    for word in words:
        candidate = word if not current else " ".join(current + [word])
        if len(candidate) <= max_chars:
            current.append(word)
        else:
            if current:
                chunks.append(" ".join(current))
            # Extremely long tokens are split safely instead of exceeding the limit.
            if len(word) > max_chars:
                for i in range(0, len(word), max_chars):
                    chunks.append(word[i:i + max_chars])
                current = []
            else:
                current = [word]

    if current:
        chunks.append(" ".join(current))
    return chunks


def extract(path):
    parts = []
    reader = PdfReader(str(path))
    for page in reader.pages:
        parts.append((page.extract_text() or "").strip())
    return "\n\n".join(x for x in parts if x).strip()


def new_job(job):
    with _lock:
        _jobs[job] = {
            "status": "Queued",
            "message": "File received. Waiting to start.",
            "current": 0,
            "total": 0,
            "error": None,
            "logs": [],
            "output": None,
        }


def update(job, **kw):
    with _lock:
        if job in _jobs:
            _jobs[job].update(kw)


def log(job, message, level="info"):
    from datetime import datetime
    with _lock:
        if job not in _jobs:
            return
        j = _jobs[job]
        j["logs"].append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": message,
            "level": level,
        })
        j["logs"] = j["logs"][-MAX_LOGS:]


def visible(page, selectors):
    for sel in selectors:
        loc = page.locator(sel)
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    return el
            except Exception:
                pass
    return None


def text_or_value(el):
    try:
        value = el.input_value(timeout=500)
        if value:
            return value
    except Exception:
        pass
    try:
        return el.inner_text(timeout=500)
    except Exception:
        try:
            return el.text_content(timeout=500) or ""
        except Exception:
            return ""


def find_button(page, wanted):
    loc = page.locator("button, [role='button'], input[type='submit'], input[type='button']")
    for i in range(loc.count()):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            bits = [
                el.inner_text() or "",
                el.get_attribute("value") or "",
                el.get_attribute("aria-label") or "",
                el.get_attribute("title") or "",
            ]
            label = " ".join(bits).strip().casefold()
            if wanted.casefold() in label:
                return el
        except Exception:
            pass
    return None


def get_input(page):
    # ExampdfX currently exposes a textarea for the source text.
    return visible(page, [
        "textarea:not([readonly]):not([disabled])",
        "textarea",
        "[contenteditable='true']",
    ])


def collect_output_candidates(page, original):
    candidates = []
    selectors = [
        "#output",
        "#result",
        "#paraphrased",
        "#paraphrase-output",
        ".output",
        ".result",
        ".rewritten",
        "[class*='output' i]",
        "[class*='result' i]",
        "[class*='paraph' i]",
        "textarea[readonly]",
        "textarea[disabled]",
        "[contenteditable='true']",
    ]

    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(loc.count()):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    txt = text_or_value(el).strip()
                    if not txt:
                        continue
                    if txt == original.strip():
                        continue
                    if wc(txt) < 5:
                        continue
                    # Avoid accidentally treating the page's own UI copy as the result.
                    if len(txt) > max(len(original) * 2.5, 12000):
                        continue
                    candidates.append(txt)
                except Exception:
                    pass
        except Exception:
            pass

    # Prefer the longest plausible rewritten text.
    candidates.sort(key=lambda x: (wc(x), len(x)), reverse=True)
    return candidates


def process_chunk(job, page, text, number, total):
    page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
    page.wait_for_timeout(1500)

    inp = get_input(page)
    if not inp:
        raise RuntimeError("ExampdfX input box not detected")

    inp.fill(text)
    log(job, f"Chunk {number}/{total}: entered {len(text)} characters")

    # Formal mode is appropriate for an academic/project document.
    mode = find_button(page, "Formal")
    if mode:
        try:
            mode.click()
            page.wait_for_timeout(300)
            log(job, "Formal rewrite mode selected")
        except Exception as e:
            log(job, f"Could not select Formal mode: {e}", "info")

    btn = find_button(page, "Paraphrase Now")
    if not btn:
        raise RuntimeError("ExampdfX 'Paraphrase Now' button not detected")

    # Snapshot current visible output-like fields before submission.
    btn.click(force=True)
    log(job, f"Chunk {number}/{total}: submitted to ExampdfX")

    deadline_ms = RESULT_TIMEOUT_SECONDS * 1000
    elapsed = 0
    while elapsed < deadline_ms:
        page.wait_for_timeout(1000)
        elapsed += 1000

        candidates = collect_output_candidates(page, text)
        if candidates:
            result = candidates[0]
            # Ensure we don't accept a tiny UI fragment.
            if wc(result) >= max(5, int(wc(text) * 0.25)):
                return result

    # Diagnostic screenshot/HTML make failures visible in Railway logs/files.
    diag = WORK_DIR / f"{job}_chunk_{number}_debug.png"
    try:
        page.screenshot(path=str(diag), full_page=False)
        log(job, f"Chunk {number}: result not detected after {RESULT_TIMEOUT_SECONDS}s; diagnostic screenshot saved", "error")
    except Exception:
        pass

    raise RuntimeError(f"ExampdfX did not return rewritten text for chunk {number} within {RESULT_TIMEOUT_SECONDS} seconds")


def make_docx(parts, path):
    doc = Document()
    title = doc.add_heading("Processed Document", level=1)
    for i, part in enumerate(parts):
        if i:
            doc.add_paragraph()
        for paragraph in re.split(r"\n\s*\n", part):
            if paragraph.strip():
                doc.add_paragraph(paragraph.strip())
    doc.save(path)


def run_job(job, src, out):
    try:
        update(job, status="Reading", message="Extracting PDF text...")
        text = extract(src)
        if not text:
            raise RuntimeError("No extractable text found in PDF")

        chunks = split_chunks(text)
        if not chunks:
            raise RuntimeError("No text chunks were created")

        update(
            job,
            status="Running",
            message=f"Starting ExampdfX processing: {len(chunks)} chunks",
            current=0,
            total=len(chunks),
        )
        log(job, f"Job started: {len(chunks)} chunks, max {MAX_CHARS} characters each")

        done = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = context.new_page()
            try:
                for n, chunk in enumerate(chunks, 1):
                    update(
                        job,
                        message=f"Processing chunk {n}/{len(chunks)}",
                        current=n - 1,
                        total=len(chunks),
                    )
                    log(job, f"Starting chunk {n}/{len(chunks)} ({len(chunk)} chars, {wc(chunk)} words)")
                    result = process_chunk(job, page, chunk, n, len(chunks))
                    done.append(result)
                    update(job, current=n)
                    log(job, f"Chunk {n}/{len(chunks)} completed ({wc(result)} output words)", "success")
            finally:
                context.close()
                browser.close()

        make_docx(done, out)
        update(
            job,
            status="Completed",
            message="All chunks processed by ExampdfX",
            current=len(chunks),
            total=len(chunks),
            output=str(out),
        )
        log(job, "DOCX created successfully", "success")

    except Exception as e:
        update(job, status="Error", message=str(e), error=str(e))
        log(job, f"Error: {e}", "error")
    finally:
        try:
            src.unlink()
        except Exception:
            pass


@app.post("/api/inspect")
def inspect():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
            page.wait_for_timeout(1500)
            inp = get_input(page)
            btn = find_button(page, "Paraphrase Now")
            formal = find_button(page, "Formal")
            return jsonify({
                "url": page.url,
                "title": page.title(),
                "inputDetected": bool(inp),
                "buttonDetected": bool(btn),
                "formalModeDetected": bool(formal),
                "maxCharacters": MAX_CHARS,
                "siteLimitCharacters": 3000,
            })
        finally:
            browser.close()


@app.post("/api/process-pdf")
def process_pdf():
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Upload a PDF file"}), 400

    job = uuid.uuid4().hex
    src = WORK_DIR / f"{job}.pdf"
    out = WORK_DIR / f"{job}_processed.docx"
    f.save(src)
    new_job(job)

    Thread(target=run_job, args=(job, src, out), daemon=True).start()
    return jsonify({
        "jobId": job,
        "status": "Queued",
        "message": "Upload complete. Background ExampdfX job started.",
    }), 202


@app.get("/api/jobs/<job>")
def job_status(job):
    with _lock:
        data = _jobs.get(job)
        if not data:
            return jsonify({"error": "Job not found"}), 404
        return jsonify({k: v for k, v in data.items() if k != "output"})


@app.get("/api/jobs/<job>/download")
def job_download(job):
    with _lock:
        data = _jobs.get(job)
    if not data:
        return jsonify({"error": "Job not found"}), 404
    if data["status"] != "Completed" or not data.get("output"):
        return jsonify({"error": "File not ready"}), 409

    path = Path(data["output"])
    if not path.exists():
        return jsonify({"error": "Output file no longer exists"}), 404

    return send_file(
        path,
        as_attachment=True,
        download_name="exampdfx_processed_document.docx",
    )


@app.get("/api/logs")
def logs():
    with _lock:
        if not _jobs:
            return jsonify({
                "status": "Idle",
                "message": "",
                "current": 0,
                "total": 0,
                "logs": [],
            })
        job = next(reversed(_jobs))
        return jsonify(_jobs[job])


@app.get("/api/status")
def status():
    return logs()


@app.get("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
