import os
import re
import uuid
from pathlib import Path
from threading import Lock, Thread

import torch
from flask import Flask, jsonify, request, send_file, send_from_directory
from docx import Document
from pypdf import PdfReader
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

APP_DIR = Path(__file__).resolve().parent
WORK_DIR = APP_DIR / "work"
WORK_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
_lock = Lock()
_model_lock = Lock()
_jobs = {}

MODEL_ID = os.getenv("MODEL_ID", "Vamsi/T5_Paraphrase_Paws")
HF_HOME = os.getenv("HF_HOME", str(APP_DIR / "hf-cache"))
os.environ["HF_HOME"] = HF_HOME

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "4"))
MAX_UNIT_WORDS = int(os.getenv("MAX_UNIT_WORDS", "180"))
MAX_INPUT_TOKENS = int(os.getenv("MAX_INPUT_TOKENS", "384"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "256"))

_tokenizer = None
_model = None
_device = None


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


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

        candidate = words if not current else current + words
        if len(candidate) <= max_words:
            current = candidate
        else:
            flush()
            current = words

    flush()
    return units


def init_model():
    global _tokenizer, _model, _device
    with _model_lock:
        if _model is not None:
            return

        torch.set_num_threads(max(1, int(os.getenv("TORCH_THREADS", "2"))))
        _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        _model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_ID).to(_device)
        _model.eval()


def paraphrase_batch(texts):
    init_model()

    prompts = ["paraphrase: " + clean_text(t) for t in texts]
    encoded = _tokenizer(
        prompts,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=MAX_INPUT_TOKENS,
    )
    encoded = {k: v.to(_device) for k, v in encoded.items()}

    with torch.inference_mode():
        outputs = _model.generate(
            **encoded,
            num_beams=4,
            max_new_tokens=MAX_OUTPUT_TOKENS,
            repetition_penalty=1.15,
            no_repeat_ngram_size=3,
            early_stopping=True,
        )

    results = _tokenizer.batch_decode(outputs, skip_special_tokens=True)
    final = []

    for original, rewritten in zip(texts, results):
        rewritten = clean_text(rewritten)

        if word_count(rewritten) < max(5, int(word_count(original) * 0.25)):
            final.append(original)
            continue

        if rewritten.casefold() == clean_text(original).casefold():
            final.append(original)
            continue

        final.append(rewritten)

    return final


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
    first = True

    for page_no, blocks in page_results:
        if not first:
            doc.add_page_break()
        first = False

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
            message=f"Starting local paraphrasing: {total} text chunks",
            current=0,
            total=total,
        )
        log(job, f"Loaded PDF: {len(pages)} pages, {total} chunks")
        log(job, f"Local model: {MODEL_ID}")

        results_by_page = {}
        completed = 0

        update(job, message="Loading paraphrasing model (first run can take a while)...")
        init_model()
        log(job, "Paraphrasing model loaded", "success")

        for start in range(0, total, BATCH_SIZE):
            batch = page_units[start:start + BATCH_SIZE]
            texts = [x[1] for x in batch]
            rewritten = paraphrase_batch(texts)

            for (page_no, _), result in zip(batch, rewritten):
                results_by_page.setdefault(page_no, []).append(result)

            completed += len(batch)
            update(
                job,
                current=completed,
                message=f"Paraphrasing chunk {completed}/{total}",
            )
            log(job, f"Completed {completed}/{total} chunks", "success")

        page_results = [
            (page_no, results_by_page.get(page_no, []))
            for page_no in range(1, len(pages) + 1)
        ]
        make_docx(page_results, out)

        update(
            job,
            status="Completed",
            message="Processing complete — DOCX is ready",
            current=total,
            total=total,
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
        "model": MODEL_ID,
        "modelLoaded": _model is not None,
        "device": str(_device) if _device else None,
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
