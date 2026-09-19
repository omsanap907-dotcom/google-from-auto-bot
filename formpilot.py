import os
import re
import uuid
from pathlib import Path
from threading import Lock
from flask import Flask, jsonify, request, send_from_directory, send_file
from pypdf import PdfReader
from docx import Document
from playwright.sync_api import sync_playwright

APP_DIR=Path(__file__).resolve().parent
WORK_DIR=APP_DIR/"work"; WORK_DIR.mkdir(exist_ok=True)
app=Flask(__name__)
_lock=Lock()
_state={"status":"Idle","message":"","current":0,"total":0,"error":None}
TARGET_URL="https://www.plagiarismremover.co/"
MAX_WORDS=500

def set_state(**kw):
    with _lock: _state.update(kw)
def wc(s): return len(re.findall(r"\S+",s))
def chunks(s):
    w=re.findall(r"\S+",s)
    return [" ".join(w[i:i+MAX_WORDS]) for i in range(0,len(w),MAX_WORDS)]
def extract(path):
    return "\n\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages).strip()
def visible(page, selectors):
    for sel in selectors:
        loc=page.locator(sel)
        for i in range(loc.count()):
            el=loc.nth(i)
            try:
                if el.is_visible(): return el
            except: pass
    return None
def val(el):
    try: return el.input_value()
    except:
        try: return el.inner_text()
        except: return ""
def button(page):
    for sel in ["button","[role='button']","input[type='submit']","input[type='button']"]:
        loc=page.locator(sel)
        for i in range(loc.count()):
            el=loc.nth(i)
            try:
                if not el.is_visible(): continue
                t=" ".join([el.inner_text() or "",el.get_attribute("value") or "",el.get_attribute("aria-label") or "",el.get_attribute("title") or ""]).casefold()
                if "remove plagiarism" in t or "plagiarism remover" in t or t.strip()=="remove": return el
            except: pass
    return None
def process_chunk(page,text):
    page.goto(TARGET_URL,wait_until="domcontentloaded",timeout=30000); page.wait_for_timeout(1800)
    inp=visible(page,["textarea","[contenteditable='true']","input[type='text']"])
    if not inp: raise RuntimeError("Target input not detected")
    inp.fill(text); page.wait_for_timeout(300)
    btn=button(page)
    if not btn: raise RuntimeError("Target processing button not detected")
    btn.click(force=True)
    selectors=["textarea","[contenteditable='true']","[id*='result' i]","[class*='result' i]","[id*='output' i]","[class*='output' i]"]
    for _ in range(60):
        page.wait_for_timeout(1000)
        for sel in selectors:
            loc=page.locator(sel)
            for i in range(loc.count()):
                el=loc.nth(i)
                try:
                    if el.is_visible():
                        out=val(el).strip()
                        if out and out!=text.strip() and wc(out)>5: return out
                except: pass
    raise RuntimeError("No changed output detected within 60 seconds")
def make_docx(parts,path):
    doc=Document()
    for i,part in enumerate(parts):
        if i: doc.add_paragraph()
        for line in part.split("\n"):
            if line.strip(): doc.add_paragraph(line.strip())
    doc.save(path)

@app.post("/api/inspect")
def inspect():
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True); page=b.new_page(viewport={"width":1280,"height":900})
        try:
            page.goto(TARGET_URL,wait_until="domcontentloaded",timeout=30000); page.wait_for_timeout(1800)
            return jsonify({"url":page.url,"title":page.title(),"inputDetected":bool(visible(page,["textarea","[contenteditable='true']","input[type='text']"])),"buttonDetected":bool(button(page)),"limit":MAX_WORDS})
        finally: b.close()

@app.post("/api/process-pdf")
def process_pdf():
    f=request.files.get("file")
    if not f or not f.filename.lower().endswith(".pdf"): return jsonify({"error":"Upload a PDF file"}),400
    job=uuid.uuid4().hex; src=WORK_DIR/f"{job}.pdf"; out=WORK_DIR/f"{job}_processed.docx"; f.save(src)
    done=[]
    try:
        text=extract(src)
        if not text: raise RuntimeError("No extractable text found")
        cs=chunks(text); set_state(status="Running",message="Starting",current=0,total=len(cs),error=None)
        with sync_playwright() as p:
            b=p.chromium.launch(headless=True); page=b.new_page(viewport={"width":1280,"height":900})
            try:
                for n,c in enumerate(cs,1):
                    set_state(message=f"Processing chunk {n}/{len(cs)}",current=n,total=len(cs))
                    done.append(process_chunk(page,c))
            finally: b.close()
        make_docx(done,out); set_state(status="Completed",message="All chunks processed",current=len(cs),total=len(cs))
        return send_file(out,as_attachment=True,download_name="processed_document.docx")
    except Exception as e:
        set_state(status="Error",message=str(e),error=str(e))
        return jsonify({"error":str(e),"completedChunks":len(done)}),500
    finally:
        try: src.unlink()
        except: pass

@app.get("/api/status")
def status():
    with _lock: return jsonify(dict(_state))
@app.get("/")
def index(): return send_from_directory(APP_DIR,"index.html")
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
