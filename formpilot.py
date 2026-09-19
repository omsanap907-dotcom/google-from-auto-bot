import os
import re
import uuid
from pathlib import Path
from threading import Lock, Thread
from queue import Queue, Empty
from flask import Flask, jsonify, request, send_from_directory, send_file
from pypdf import PdfReader
from docx import Document
from playwright.sync_api import sync_playwright

APP_DIR=Path(__file__).resolve().parent
WORK_DIR=APP_DIR/"work"; WORK_DIR.mkdir(exist_ok=True)
app=Flask(__name__)
_lock=Lock()
_jobs={}
_controls={}
TARGET_URL="https://www.plagiarismremover.co/"
MAX_WORDS=500
MAX_LOGS=200

def wc(s): return len(re.findall(r"\S+",s))
def chunks(s):
    w=re.findall(r"\S+",s)
    return [" ".join(w[i:i+MAX_WORDS]) for i in range(0,len(w),MAX_WORDS)]
def extract(path):
    return "\n\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages).strip()

def new_job(job):
    with _lock:
        _jobs[job]={"status":"Queued","message":"File received. Waiting to start.","current":0,"total":0,"error":None,"logs":[],"output":None}
        _controls[job]={"commands":Queue()}

def update(job,**kw):
    with _lock: _jobs[job].update(kw)
def log(job,message,level="info"):
    from datetime import datetime
    with _lock:
        j=_jobs[job]
        j["logs"].append({"time":datetime.now().strftime("%H:%M:%S"),"message":message,"level":level})
        j["logs"]=j["logs"][-MAX_LOGS:]

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

def captcha_present(page):
    try:
        if page.locator("iframe[src*='recaptcha' i], iframe[title*='recaptcha' i], .g-recaptcha").count():
            return True
        body=(page.locator("body").inner_text(timeout=2000) or "").casefold()
        return any(x in body for x in ["verify you are human","captcha","i'm not a robot","im not a robot"])
    except:
        return False

def save_captcha_screenshot(job,page):
    path=WORK_DIR/f"{job}_captcha.png"
    try:
        page.screenshot(path=str(path),full_page=False)
        return path
    except:
        return None

def wait_for_captcha(job,page):
    update(job,status="CAPTCHA",message="CAPTCHA required — open the CAPTCHA panel and complete it.")
    log(job,"CAPTCHA detected. Waiting for manual user interaction.","info")
    save_captcha_screenshot(job,page)
    import time
    deadline=time.time()+600
    while time.time()<deadline:
        try:
            q=_controls.get(job,{}).get("commands")
            if q:
                while True:
                    cmd=q.get_nowait()
                    if cmd.get("type")=="click":
                        x=float(cmd["x"]); y=float(cmd["y"])
                        page.mouse.click(x,y)
                        log(job,f"Clicked browser at ({int(x)}, {int(y)})")
                    elif cmd.get("type")=="scroll":
                        page.mouse.wheel(0,float(cmd.get("delta",600)))
                        log(job,"Scrolled CAPTCHA browser")
                    elif cmd.get("type")=="reload":
                        page.reload(wait_until="domcontentloaded",timeout=30000)
                        log(job,"Browser page reloaded")
        except Empty:
            pass
        except Exception as e:
            log(job,f"Browser action error: {e}","error")
        page.wait_for_timeout(1000)
        if not captcha_present(page):
            log(job,"CAPTCHA cleared. Resuming processing.","success")
            update(job,status="Running",message="CAPTCHA cleared — waiting for result.")
            return
        save_captcha_screenshot(job,page)
    raise RuntimeError("CAPTCHA was not completed within 10 minutes")

def process_chunk(job,page,text):
    page.goto(TARGET_URL,wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(1800)
    inp=visible(page,["textarea","[contenteditable='true']","input[type='text']"])
    if not inp: raise RuntimeError("Target input not detected")
    inp.fill(text)
    btn=button(page)
    if not btn: raise RuntimeError("Target processing button not detected")
    btn.click(force=True)
    page.wait_for_timeout(1500)
    if captcha_present(page):
        wait_for_captcha(job,page)
    selectors=["textarea","[contenteditable='true']","[id*='result' i]","[class*='result' i]","[id*='output' i]","[class*='output' i]"]
    for _ in range(90):
        page.wait_for_timeout(1000)
        if captcha_present(page):
            wait_for_captcha(job,page)
        for sel in selectors:
            loc=page.locator(sel)
            for i in range(loc.count()):
                el=loc.nth(i)
                try:
                    if el.is_visible():
                        out=val(el).strip()
                        if out and out!=text.strip() and wc(out)>5: return out
                except: pass
    raise RuntimeError("No changed output detected within 90 seconds")

def make_docx(parts,path):
    doc=Document()
    for i,part in enumerate(parts):
        if i: doc.add_paragraph()
        for line in part.split("\n"):
            if line.strip(): doc.add_paragraph(line.strip())
    doc.save(path)

def run_job(job,src,out):
    try:
        update(job,status="Reading",message="Extracting PDF text...")
        text=extract(src)
        if not text: raise RuntimeError("No extractable text found")
        cs=chunks(text)
        update(job,status="Running",message=f"Starting processing: {len(cs)} chunks",current=0,total=len(cs))
        log(job,f"Job started: {len(cs)} chunks found")
        done=[]
        with sync_playwright() as p:
            b=p.chromium.launch(headless=False)
            page=b.new_page(viewport={"width":1280,"height":900})
            try:
                for n,c in enumerate(cs,1):
                    update(job,message=f"Processing chunk {n}/{len(cs)}",current=n-1,total=len(cs))
                    log(job,f"Starting chunk {n}/{len(cs)} ({wc(c)} words)")
                    result=process_chunk(job,page,c)
                    done.append(result)
                    update(job,current=n)
                    log(job,f"Chunk {n}/{len(cs)} completed","success")
            finally: b.close()
        make_docx(done,out)
        update(job,status="Completed",message="All chunks processed",current=len(cs),total=len(cs),output=str(out))
        log(job,"DOCX created successfully","success")
    except Exception as e:
        update(job,status="Error",message=str(e),error=str(e))
        log(job,f"Error: {e}","error")
    finally:
        try: src.unlink()
        except: pass
        try: (WORK_DIR/f"{job}_captcha.png").unlink()
        except: pass
        _controls.pop(job,None)

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
    if not f or not f.filename.lower().endswith(".pdf"):
        return jsonify({"error":"Upload a PDF file"}),400
    job=uuid.uuid4().hex
    src=WORK_DIR/f"{job}.pdf"; out=WORK_DIR/f"{job}_processed.docx"
    f.save(src)
    new_job(job)
    Thread(target=run_job,args=(job,src,out),daemon=True).start()
    return jsonify({"jobId":job,"status":"Queued","message":"Upload complete. Background job started."}),202

@app.get("/api/jobs/<job>/captcha")
def captcha_status(job):
    with _lock:
        data=_jobs.get(job)
    if not data: return jsonify({"error":"Job not found"}),404
    path=WORK_DIR/f"{job}_captcha.png"
    return jsonify({"required":data.get("status")=="CAPTCHA","ready":path.exists(),"message":data.get("message","")})

@app.get("/api/jobs/<job>/captcha/image")
def captcha_image(job):
    with _lock:
        if job not in _jobs: return jsonify({"error":"Job not found"}),404
    path=WORK_DIR/f"{job}_captcha.png"
    if not path.exists(): return jsonify({"error":"CAPTCHA screenshot not ready"}),404
    return send_file(path,mimetype="image/png",max_age=0)

@app.post("/api/jobs/<job>/captcha/action")
def captcha_action(job):
    with _lock:
        if job not in _jobs: return jsonify({"error":"Job not found"}),404
        control=_controls.get(job)
    if not control: return jsonify({"error":"No active browser session"}),409
    data=request.get_json(silent=True) or {}
    action=data.get("action","click")
    if action=="click":
        try:
            x=float(data["x"]); y=float(data["y"])
        except:
            return jsonify({"error":"x and y are required"}),400
        control["commands"].put({"type":"click","x":x,"y":y})
    elif action=="scroll":
        control["commands"].put({"type":"scroll","delta":float(data.get("delta",600))})
    elif action=="reload":
        control["commands"].put({"type":"reload"})
    else:
        return jsonify({"error":"Unknown action"}),400
    return jsonify({"ok":True})

@app.get("/api/jobs/<job>")
def job_status(job):
    with _lock:
        data=_jobs.get(job)
        if not data: return jsonify({"error":"Job not found"}),404
        return jsonify({k:v for k,v in data.items() if k!="output"})

@app.get("/api/jobs/<job>/download")
def job_download(job):
    with _lock: data=_jobs.get(job)
    if not data: return jsonify({"error":"Job not found"}),404
    if data["status"]!="Completed" or not data.get("output"): return jsonify({"error":"File not ready"}),409
    path=Path(data["output"])
    if not path.exists(): return jsonify({"error":"Output file no longer exists"}),404
    return send_file(path,as_attachment=True,download_name="processed_document.docx")

@app.get("/api/logs")
def logs():
    with _lock:
        if not _jobs: return jsonify({"status":"Idle","message":"","current":0,"total":0,"logs":[]})
        job=next(reversed(_jobs))
        return jsonify(_jobs[job])

@app.get("/api/status")
def status():
    return logs()

@app.get("/")
def index(): return send_from_directory(APP_DIR,"index.html")

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
