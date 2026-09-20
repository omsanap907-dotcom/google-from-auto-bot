import os
import re
import uuid
from pathlib import Path
from threading import Lock, Thread

from flask import Flask, jsonify, request, send_file, send_from_directory
from docx import Document
from llama_cpp import Llama
from pypdf import PdfReader

APP_DIR = Path(__file__).resolve().parent
WORK_DIR = APP_DIR / "work"
WORK_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
_lock = Lock()
_model_lock = Lock()
_jobs = {}

MODEL_REPO = os.getenv("MODEL_REPO", "tensorblock/T5_Paraphrase_Paws-GGUF")
MODEL_FILE = os.getenv("MODEL_FILE", "T5_Paraphrase_Paws-Q4_K_M.gguf")
MODEL_CTX = int(os.getenv("MODEL_CTX", "512"))
MODEL_THREADS = int(os.getenv("MODEL_THREADS", "4"))
MAX_UNIT_WORDS = int(os.getenv("MAX_UNIT_WORDS", "110"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "220"))

_model = None


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


def extract_pages(path):
    reader = PdfReader(str(path))
    return [(page.extract_text() or "").strip() for page in reader.pages]


def split_sentences(text):
    text = clean_text(text)
    if not text:
        return []

    protected = re.sub(
        r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|Fig|Eq|No|vs|etc)\.",
        lambda m: m.group(1) + "<DOT>",
        text,
        flags=re.I,
    )
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", protected)
    return [p.replace("<DOT>", ".").strip() for p in parts if p.strip()]


def make_units(text, max_words=MAX_UNIT_WORDS):
    sentences = split_sentences(text)
    units = []
    current = []

    def flush():
        nonlocal current
        if current:
            units.append(" ".join(current).strip())
            current = []

    for sentence in sentences:
        words = sentence.split()
        if not words:
            continue

        if len(words) > max_words:
            flush()
            for i in range(0, len(words), max_words):
                units.append(" ".join(words[i:i + max_words]))
            continue

        candidate = current + words
        if len(candidate) <= max_words:
            current = candidate
        else:
            flush()
            current = words

    flush()
    return units


def init_model(job=None):
    global _model

    with _model_lock:
        if _model is not None:
            return

        if job:
            update(job, message="Loading local paraphrasing model (first run downloads about 137 MB)...")
            log(job, f"Model: {MODEL_REPO}/{MODEL_FILE}")

        _model = Llama.from_pretrained(
            repo_id=MODEL_REPO,
            filename=MODEL_FILE,
            n_ctx=MODEL_CTX,
            n_threads=MODEL_THREADS,
            verbose=False,
        )

        if job:
            log(job, "Local GGUF model loaded", "success")


def paraphrase_one(text):
    init_model()

    prompt = "paraphrase: " + clean_text(text)

    result = _model(
        prompt,
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.7,
        top_p=0.95,
        repeat_penalty=1.1,
        echo=False,
        stop=["</s>"],
    )

    rewritten = clean_text(result["choices"][0]["text"])

    if not rewritten:
        return text

    # Never replace useful source text with an obviously truncated response.
    if word_count(rewritten) < max(5, int(word_count(text) * 0.30)):
        return text

    if rewritten.casefold() == clean_text(text).casefold():
        return text

    return rewritten


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


def update(job, **kwargs):
    with _lock:
        if job in _jobs:
            _jobs[job].update(kwargs)


def log(job, message, level="info"):
    from datetime import datetime

    with _lock:
        if job not in _jobs:
            return

        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": message,
            "level": level,
        }
        _jobs[job]["logs"].append(entry)
        _jobs[job]["logs"] = _jobs[job]["logs"][-300:]
        print(f"[{entry['time']}] {level.upper()}: {message}", flush=True)


def make_docx(page_results, path):
    doc = Document()
    first_page = True

    for page_no, blocks in page_results:
        if not first_page:
            doc.add_page_break()
        first_page = False

        for block in blocks:
            if block.strip():
                doc.add_paragraph(block.strip())

    doc.save(path)


def run_job(job, src, out):
    try:
        pages = extract_pages(src)

        if not any(clean_text(p) for p in pages):
            raise RuntimeError("No extractable text found in PDF")

        page_units = []
        for page_no, page_text in enumerate(pages, 1):
            for unit in make_units(page_text):
                page_units.append((page_no, unit))

        if not page_units:
            raise RuntimeError("No text chunks were created")

        total = len(page_units)
        update(
            job,
            status="Running",
            current=0,
            total=total,
            message=f"Starting local paraphrasing: {total} chunks",
        )
        log(job, f"PDF loaded: {len(pages)} pages / {total} chunks")

        init_model(job)

        results_by_page = {}
        completed = 0

        for page_no, unit in page_units:
            rewritten = paraphrase_one(unit)
            results_by_page.setdefault(page_no, []).append(rewritten)

            completed += 1
            update(
                job,
                current=completed,
                message=f"Paraphrasing chunk {completed}/{total}",
            )
            log(job, f"Completed chunk {completed}/{total}", "success")

        page_results = [
            (page_no, results_by_page.get(page_no, []))
            for page_no in range(1, len(pages) + 1)
        ]
        make_docx(page_results, out)

        update(
            job,
            status="Completed",
            current=total,
            total=total,
            message="Processing complete — DOCX is ready",
            output=str(out),
        )
        log(job, "DOCX created successfully", "success")

    except Exception as exc:
        update(job, status="Error", message=str(exc), error=str(exc))
        log(job, f"Error: {exc}", "error")
    finally:
        try:
            src.unlink()
        except Exception:
            pass


@app.get("/")
def index():
    response = send_from_directory(APP_DIR, "index.html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "engine": "local-gguf",
        "modelRepo": MODEL_REPO,
        "modelFile": MODEL_FILE,
        "modelLoaded": _model is not None,
    })


@app.post("/api/process-pdf")
def process_pdf():
    uploaded = request.files.get("file")

    if not uploaded or not uploaded.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a PDF file"}), 400

    job = uuid.uuid4().hex
    src = WORK_DIR / f"{job}.pdf"
    out = WORK_DIR / f"{job}_processed.docx"

    uploaded.save(src)
    new_job(job)

    Thread(target=run_job, args=(job, src, out), daemon=True).start()

    return jsonify({
        "jobId": job,
        "status": "Queued",
        "message": "Upload complete. Local paraphrasing job started.",
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
        download_name="processed_document.docx",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
