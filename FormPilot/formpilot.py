import json
import re
import time
import random
from pathlib import Path
from threading import Thread, Event, Lock
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from playwright.sync_api import sync_playwright


# ============================================================
# FORM PILOT
# UNIVERSAL GOOGLE FORMS JSON AUTOMATION
# ============================================================

APP_DIR = Path(__file__).resolve().parent

app = Flask(__name__)

state_lock = Lock()
stop_event = Event()
worker_thread = None

state = {
    "status": "Idle",
    "current": 0,
    "total": 0,
    "progress": 0,
    "logs": [],
    "error": None,
    "mode": "test"
}


# ============================================================
# LOGGING / STATE
# ============================================================

def now():
    return datetime.now().strftime("%H:%M:%S")


def log(message):
    with state_lock:
        state["logs"].append({
            "time": now(),
            "message": str(message)
        })

        state["logs"] = state["logs"][-200:]


def set_state(**kwargs):
    with state_lock:
        state.update(kwargs)


# ============================================================
# TEXT HELPERS
# ============================================================

def clean(value):
    """
    Normalize text for matching.
    """
    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value)
    ).strip().casefold()


# ============================================================
# LOAD JSON
# ============================================================

def load_data(filename):
    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):

        if isinstance(data.get("responses"), list):
            return data["responses"]

        if isinstance(data.get("data"), list):
            return data["data"]

        return [data]

    if not isinstance(data, list):
        raise ValueError(
            "JSON must contain a list of response objects."
        )

    return data


# ============================================================
# GOOGLE FORM QUESTION BLOCKS
# ============================================================

def get_question_blocks(page):
    """
    Google Forms normally puts each question inside:
        [role="listitem"]

    We use role=listitem rather than Google's internal CSS
    classes because those classes can change.
    """

    blocks = page.locator('[role="listitem"]')

    return [
        blocks.nth(i)
        for i in range(blocks.count())
    ]


def question_text(block):
    try:
        return block.inner_text(
            timeout=1500
        ).strip()

    except Exception:
        return ""


# ============================================================
# FIND QUESTION
# ============================================================

def find_question(blocks, key, used):
    """
    Match JSON field name to Google Form question.

    Examples:

        Q1_Age
        Q2_Gender
        Q17_Likelihood

    """

    key_clean = clean(key)

    # Remove Q1_, Q2_, etc.
    key_words = re.sub(
        r"^q\d+[_\-\s]*",
        "",
        key_clean
    )

    key_words = (
        key_words
        .replace("_", " ")
        .replace("-", " ")
    )

    candidates = []

    for i, block in enumerate(blocks):

        if i in used:
            continue

        text = clean(
            question_text(block)
        )

        if not text:
            continue

        score = 0

        # Exact match
        if key_clean == text:
            score += 100

        # Full key phrase
        if key_words and key_words in text:
            score += 50

        # Individual words
        words = [
            w
            for w in key_words.split()
            if len(w) > 2
        ]

        score += sum(
            3
            for w in words
            if w in text
        )

        if score:
            candidates.append(
                (score, i, block)
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return (
        candidates[0][2],
        candidates[0][1]
    )


# ============================================================
# FIND VISIBLE OPTIONS
# ============================================================

def visible_option_labels(block):

    labels = []

    selectors = [
        '[role="radio"]',
        '[role="checkbox"]',
        '[role="option"]',
        'label'
    ]

    for selector in selectors:

        loc = block.locator(selector)

        for i in range(loc.count()):

            try:

                txt = loc.nth(i).inner_text(
                    timeout=500
                ).strip()

                if txt and txt not in labels:
                    labels.append(txt)

            except Exception:
                pass

    return labels


# ============================================================
# CLICK OPTION
# ============================================================

def click_matching_option(
    block,
    value,
    multiple=False
):

    values = (
        value
        if isinstance(value, list)
        else [value]
    )

    for wanted in values:

        wanted_clean = clean(wanted)

        options = []

        selectors = [
            '[role="radio"]',
            '[role="checkbox"]',
            '[role="option"]',
            'label'
        ]

        for selector in selectors:

            loc = block.locator(selector)

            for i in range(loc.count()):

                try:

                    el = loc.nth(i)

                    txt = clean(
                        el.inner_text(
                            timeout=500
                        )
                    )

                    if txt:
                        options.append(
                            (txt, el)
                        )

                except Exception:
                    pass

        # Exact match
        match = next(
            (
                (txt, el)
                for txt, el in options
                if txt == wanted_clean
            ),
            None
        )

        # Partial match
        if not match:

            match = next(
                (
                    (txt, el)
                    for txt, el in options
                    if wanted_clean
                    and wanted_clean in txt
                ),
                None
            )

        if not match:

            raise RuntimeError(
                f'Option "{wanted}" not found. '
                f"Options detected: "
                f"{[x[0] for x in options]}"
            )

        _, element = match

        try:
            element.scroll_into_view_if_needed()
        except Exception:
            pass

        element.click(force=True)

        time.sleep(0.15)

    return True


# ============================================================
# FILL TEXT
# ============================================================

def fill_text(block, value):

    value = (
        ""
        if value is None
        else str(value)
    )

    selectors = [
        "textarea",
        'input[type="text"]',
        'input[type="email"]',
        'input[type="number"]',
        "input"
    ]

    for selector in selectors:

        loc = block.locator(selector)

        if loc.count():

            el = loc.first

            try:

                if el.is_visible():

                    el.fill(value)

                    return True

            except Exception:
                pass

    # Contenteditable fallback
    loc = block.locator(
        '[contenteditable="true"]'
    )

    if loc.count():

        try:

            loc.first.fill(value)

            return True

        except Exception:
            pass

    return False


# ============================================================
# FILL DROPDOWN
# ============================================================

def fill_dropdown(block, value):

    value_clean = clean(value)

    # Native HTML select
    select = block.locator("select")

    if select.count():

        try:

            select.first.select_option(
                label=str(value)
            )

            return True

        except Exception:
            pass

    # Google Forms custom dropdown
    selectors = [
        '[role="listbox"]',
        '[role="combobox"]'
    ]

    for selector in selectors:

        loc = block.locator(selector)

        if loc.count():

            try:

                loc.first.click(
                    force=True
                )

                page = block.page

                page.wait_for_timeout(300)

                options = page.locator(
                    '[role="option"]'
                )

                for i in range(
                    options.count()
                ):

                    try:

                        opt = options.nth(i)

                        txt = clean(
                            opt.inner_text(
                                timeout=500
                            )
                        )

                        if (
                            txt == value_clean
                            or value_clean in txt
                        ):

                            opt.click(
                                force=True
                            )

                            return True

                    except Exception:
                        pass

            except Exception:
                pass

    return False


# ============================================================
# FILL ONE RECORD
# ============================================================

def fill_record(page, record):

    blocks = get_question_blocks(page)

    used = set()

    failures = []

    for key, value in record.items():

        # Ignore metadata fields
        if str(key).startswith("_"):
            continue

        found = find_question(
            blocks,
            key,
            used
        )

        if not found:

            failures.append(
                f"{key}: question not found"
            )

            continue

        block, index = found

        used.add(index)

        try:

            # ------------------------------------------------
            # CHECKBOX / MULTIPLE ANSWERS
            # ------------------------------------------------

            if isinstance(value, list):

                click_matching_option(
                    block,
                    value,
                    multiple=True
                )

                continue

            # ------------------------------------------------
            # DROPDOWN
            # ------------------------------------------------

            if fill_dropdown(
                block,
                value
            ):

                continue

            # ------------------------------------------------
            # RADIO / CHECKBOX
            # ------------------------------------------------

            options = visible_option_labels(
                block
            )

            if options:

                try:

                    click_matching_option(
                        block,
                        value
                    )

                    continue

                except Exception:
                    pass

            # ------------------------------------------------
            # TEXT
            # ------------------------------------------------

            if fill_text(
                block,
                value
            ):

                continue

            failures.append(
                f"{key}: could not fill "
                f"value {value!r}"
            )

        except Exception as exc:

            failures.append(
                f"{key}: {exc}"
            )

    return failures


# ============================================================
# REQUIRED FIELD / ANSWER VALIDATION
# ============================================================

def question_title(block):
    """Return the shortest useful title for a Google Forms question block."""
    candidates = [
        '[role="heading"]',
        '.M7eMe',
        '.z12JJ',
    ]

    for selector in candidates:
        loc = block.locator(selector)
        for i in range(loc.count()):
            try:
                text = clean(loc.nth(i).inner_text(timeout=500))
                if text:
                    text = re.sub(r'\\s*\\*\\s*required\\s*$', '', text, flags=re.I).strip()
                    if text:
                        return text
            except Exception:
                pass

    text = clean(question_text(block))
    text = re.sub(r'\\s+\\*\\s*required\\s*$', '', text, flags=re.I).strip()
    return text


def is_required_block(block):
    """Detect Google Forms required-question markers without relying on one CSS class."""
    selectors = [
        '[aria-label*="required" i]',
        '[data-tooltip*="required" i]',
        '[class*="required" i]',
    ]

    for selector in selectors:
        try:
            loc = block.locator(selector)
            if loc.count():
                for i in range(loc.count()):
                    try:
                        if loc.nth(i).is_visible():
                            return True
                    except Exception:
                        pass
        except Exception:
            pass

    try:
        text = block.inner_text(timeout=500)
        if re.search(r'\*\s*required\b', text, re.I):
            return True
    except Exception:
        pass

    return False


def block_has_answer(block):
    """Best-effort check that a Google Forms question currently contains an answer."""
    # Radio / checkbox
    for selector in [
        '[role="radio"][aria-checked="true"]',
        '[role="checkbox"][aria-checked="true"]',
        'input[type="radio"]:checked',
        'input[type="checkbox"]:checked',
    ]:
        try:
            if block.locator(selector).count():
                return True
        except Exception:
            pass

    # Text, number, email, date/time and similar inputs
    for selector in [
        'textarea',
        'input',
        'select',
        '[contenteditable="true"]',
    ]:
        loc = block.locator(selector)
        for i in range(loc.count()):
            try:
                el = loc.nth(i)
                if not el.is_visible():
                    continue

                if selector == 'select':
                    value = el.input_value(timeout=500)
                elif selector == '[contenteditable="true"]':
                    value = el.inner_text(timeout=500)
                else:
                    value = el.input_value(timeout=500)

                if str(value).strip():
                    return True
            except Exception:
                pass

    # Google Forms custom dropdowns sometimes expose the selected value
    # through aria-activedescendant or a non-empty value attribute.
    for selector in ['[role="combobox"]', '[role="listbox"]']:
        loc = block.locator(selector)
        for i in range(loc.count()):
            try:
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                for attr in ['aria-activedescendant', 'aria-label', 'data-value']:
                    value = (el.get_attribute(attr) or '').strip()
                    if attr == 'aria-label' and value.lower() in {
                        'choose', 'choose an option', 'select', 'select an option'
                    }:
                        continue
                    if value:
                        return True
            except Exception:
                pass

    return False


def validate_required_fields(page):
    """Return required Google Forms questions that are still unanswered."""
    missing = []
    blocks = get_question_blocks(page)

    for block in blocks:
        try:
            if not block.is_visible():
                continue
            if not is_required_block(block):
                continue
            if not block_has_answer(block):
                title = question_title(block)
                if title and title not in missing:
                    missing.append(title)
        except Exception:
            pass

    return missing


def log_validation_details(page):
    """Log both pre-submit missing fields and Google Forms validation messages."""
    missing = validate_required_fields(page)

    if missing:
        log("Required fields still unanswered:")
        for item in missing:
            log(f"  {item}")

    errors = get_validation_errors(page)
    if errors:
        log("Google Forms validation messages:")
        for error in errors:
            log(f"  {error}")

    return missing, errors


# ============================================================
# SUBMIT BUTTON DETECTION
# ============================================================

def find_submit_button(page):

    log("Looking for Submit button...")

    # Scroll to bottom first
    try:
        page.evaluate(
            "window.scrollTo(0, document.body.scrollHeight);"
        )

        page.wait_for_timeout(500)

    except Exception:
        pass

    selectors = [

        # Most common Google Forms
        '[role="button"][data-value="Submit"]',

        # Older Google Forms
        '[role="button"][jsname="M2vV3"]',

        # Generic role button
        '[role="button"]',

        # Native button
        'button',

        # Native submit
        'input[type="submit"]'
    ]

    for selector in selectors:

        loc = page.locator(selector)

        count = loc.count()

        if count == 0:
            continue

        for i in range(count):

            try:

                el = loc.nth(i)

                if not el.is_visible():
                    continue

                text = clean(
                    el.inner_text(
                        timeout=500
                    )
                )

                aria = clean(
                    el.get_attribute(
                        "aria-label"
                    )
                    or ""
                )

                data_value = clean(
                    el.get_attribute(
                        "data-value"
                    )
                    or ""
                )

                jsname = clean(
                    el.get_attribute(
                        "jsname"
                    )
                    or ""
                )

                log(
                    "Button candidate: "
                    f"text='{text}', "
                    f"aria='{aria}', "
                    f"data-value='{data_value}', "
                    f"jsname='{jsname}'"
                )

                combined = (
                    f"{text} "
                    f"{aria} "
                    f"{data_value}"
                )

                if "submit" in combined:

                    return el

            except Exception:
                pass

    return None


# ============================================================
# VALIDATION ERROR DETECTION
# ============================================================

def get_validation_errors(page):

    errors = []

    selectors = [
        '[role="alert"]',
        '[aria-live="assertive"]',
        '[aria-live="polite"]',
        '.o6cuMc'
    ]

    for selector in selectors:

        loc = page.locator(selector)

        for i in range(loc.count()):

            try:

                if not loc.nth(i).is_visible():
                    continue

                text = loc.nth(i).inner_text(
                    timeout=500
                ).strip()

                if text and text not in errors:

                    errors.append(text)

            except Exception:
                pass

    return errors


# ============================================================
# CHECK SUBMISSION CONFIRMATION
# ============================================================

def check_submission(page):

    confirmation_patterns = [

        "Your response has been recorded",

        "Response recorded",

        "response has been recorded",

        "Thanks for submitting",

        "Thank you for submitting"
    ]

    try:

        body_text = clean(
            page.locator("body").inner_text(
                timeout=3000
            )
        )

        for pattern in confirmation_patterns:

            if clean(pattern) in body_text:

                return True

    except Exception:
        pass

    return False


# ============================================================
# SUBMIT GOOGLE FORM
# ============================================================

def submit_google_form(page, record_number):

    log(
        f"Record {record_number}: "
        "preparing submission..."
    )

    # Give Google Forms time to finish updating
    page.wait_for_timeout(700)

    # Do not click Submit when a required field is visibly unanswered.
    missing = validate_required_fields(page)
    if missing:
        log(
            f"Record {record_number}: {len(missing)} required field(s) are unanswered."
        )
        for item in missing:
            log(f"Required question missing: {item}")
        return False, "Required questions are unanswered."

    submit = find_submit_button(page)

    if not submit:

        errors = get_validation_errors(page)

        if errors:

            log(
                "Validation errors detected:"
            )

            for error in errors:
                log(f"  {error}")

        return False, "Submit button not found."

    # Check disabled state
    try:

        disabled = submit.is_disabled()

    except Exception:

        disabled = False

    try:

        aria_disabled = (
            submit.get_attribute(
                "aria-disabled"
            )
            or ""
        ).lower()

        if aria_disabled == "true":
            disabled = True

    except Exception:
        pass

    if disabled:

        errors = get_validation_errors(page)

        if errors:

            for error in errors:
                log(
                    f"Validation error: {error}"
                )

        return False, "Submit button is disabled."

    # Scroll into view
    try:
        submit.scroll_into_view_if_needed()
    except Exception:
        pass

    page.wait_for_timeout(300)

    # --------------------------------------------------------
    # CLICK
    # --------------------------------------------------------

    log(
        f"Record {record_number}: "
        "clicking Submit..."
    )

    clicked = False

    try:

        submit.click(
            timeout=5000
        )

        clicked = True

    except Exception as exc:

        log(
            f"Normal click failed: {exc}"
        )

    # Fallback JS click
    if not clicked:

        try:

            submit.evaluate(
                "(el) => el.click()"
            )

            clicked = True

            log(
                "Submit clicked using JS fallback."
            )

        except Exception as exc:

            log(
                f"JS click failed: {exc}"
            )

    if not clicked:

        return False, "Could not click Submit."

    # --------------------------------------------------------
    # WAIT FOR GOOGLE FORMS
    # --------------------------------------------------------

    page.wait_for_timeout(1500)

    # Check confirmation
    if check_submission(page):

        log(
            f"Record {record_number}: "
            "submission confirmed."
        )

        return True, "Submitted"

    # Check validation errors
    errors = get_validation_errors(page)
    missing = validate_required_fields(page)

    if errors or missing:

        log(
            f"Record {record_number}: "
            "Google Forms reported validation errors."
        )

        if missing:
            log("Required questions still unanswered:")
            for item in missing:
                log(f"  {item}")

        for error in errors:
            log(f"Validation error: {error}")

        return False, (
            "Validation errors prevented submission."
        )

    # Sometimes confirmation takes longer
    page.wait_for_timeout(1500)

    if check_submission(page):

        log(
            f"Record {record_number}: "
            "submission confirmed after waiting."
        )

        return True, "Submitted"

    log(
        f"Record {record_number}: "
        "Submit was clicked, but confirmation "
        "was not detected."
    )

    return False, (
        "Submit clicked but confirmation "
        "was not detected."
    )


# ============================================================
# RESET FORM
# ============================================================

def reset_form(page):

    page.reload(
        wait_until="domcontentloaded"
    )

    page.wait_for_timeout(1000)


# ============================================================
# MAIN AUTOMATION
# ============================================================

def run_automation(
    form_url,
    dataset,
    mode
):

    try:

        submit_form = (
            mode == "submit"
        )

        set_state(
            status="Starting",
            current=0,
            total=len(dataset),
            progress=0,
            error=None,
            mode=mode
        )

        log(
            f"Loaded {len(dataset)} records."
        )

        if submit_form:

            log(
                "Mode: FILL & SUBMIT"
            )

            log(
                "Submission mode is intended "
                "for forms you own/control."
            )

        else:

            log(
                "Mode: FILL ONLY"
            )

        log(
            "Launching Chromium..."
        )

        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=False
            )

            page = browser.new_page(
                viewport={
                    "width": 1280,
                    "height": 900
                }
            )

            try:

                # ------------------------------------------------
                # OPEN FORM
                # ------------------------------------------------

                log(
                    "Opening Google Form..."
                )

                page.goto(
                    form_url,
                    wait_until="domcontentloaded"
                )

                page.wait_for_timeout(1500)

                log(
                    "Google Form opened."
                )

                # ------------------------------------------------
                # PROCESS RECORDS
                # ------------------------------------------------

                for n, record in enumerate(
                    dataset,
                    1
                ):

                    # Stop request
                    if stop_event.is_set():

                        set_state(
                            status="Stopped"
                        )

                        log(
                            "Stop requested. "
                            "Automation ended safely."
                        )

                        break

                    set_state(
                        status="Running",
                        current=n,
                        total=len(dataset),
                        progress=round(
                            ((n - 1)
                             / len(dataset))
                            * 100
                        )
                    )

                    log(
                        f"Processing record "
                        f"{n}/{len(dataset)}..."
                    )

                    # ------------------------------------------------
                    # FILL
                    # ------------------------------------------------

                    failures = fill_record(
                        page,
                        record
                    )

                    if failures:

                        log(
                            f"Record {n} has "
                            f"{len(failures)} fill issue(s)."
                        )

                        for item in failures:

                            log(
                                f"  {item}"
                            )

                    else:

                        log(
                            f"Record {n} "
                            "filled successfully."
                        )

                    # ------------------------------------------------
                    # SUBMIT
                    # ------------------------------------------------

                    if submit_form:

                        # Never submit a record that could not be completely filled.
                        if failures:
                            set_state(
                                status="Error",
                                error=f"Record {n} has fill errors."
                            )
                            log(
                                f"Record {n}: submission skipped because filling was incomplete."
                            )
                            break

                        if stop_event.is_set():

                            set_state(
                                status="Stopped"
                            )

                            log(
                                "Stop requested "
                                "before submission."
                            )

                            break

                        success, message = (
                            submit_google_form(
                                page,
                                n
                            )
                        )

                        if not success:

                            set_state(
                                status="Error",
                                error=message
                            )

                            log(
                                f"Record {n} "
                                f"submission failed: "
                                f"{message}"
                            )

                            break

                        log(
                            f"Record {n} "
                            "submitted successfully."
                        )

                        # ------------------------------------------------
                        # NEXT RESPONSE
                        # ------------------------------------------------

                        if n < len(dataset):

                            log(
                                "Opening fresh form "
                                "for next record..."
                            )

                            page.goto(
                                form_url,
                                wait_until="domcontentloaded"
                            )

                            page.wait_for_timeout(
                                1000
                            )

                    else:

                        # Fill-only mode
                        if n < len(dataset):

                            log(
                                "Reloading form "
                                "for next record..."
                            )

                            reset_form(page)

                    set_state(
                        current=n,
                        total=len(dataset),
                        progress=round(
                            (n / len(dataset))
                            * 100
                        )
                    )

                # ------------------------------------------------
                # COMPLETE
                # ------------------------------------------------

                with state_lock:
                    current_status = state[
                        "status"
                    ]

                if current_status not in (
                    "Stopped",
                    "Error"
                ):

                    set_state(
                        status="Completed",
                        progress=100
                    )

                    log(
                        "Automation completed."
                    )

            finally:

                browser.close()

                log(
                    "Browser closed."
                )

    except Exception as exc:

        set_state(
            status="Error",
            error=str(exc)
        )

        log(
            "ERROR: "
            + str(exc)
        )

    finally:

        stop_event.clear()



# ============================================================
# API: AUTO SYNTHETIC JSON GENERATOR
# ============================================================

def _synthetic_text(question, record_number):
    q = clean(question)
    if "name" in q:
        return f"Test Respondent {record_number}"
    if "email" in q:
        return f"test{record_number}@example.com"
    if "age" in q:
        return str(18 + (record_number % 43))
    if "describe" in q or "why" in q or "reason" in q:
        return "This is a synthetic test response generated for form validation."
    if "availability" in q:
        return "2026-12-15"
    return f"Synthetic test response {record_number}"


def generate_synthetic_dataset(form_url, count):
    if count < 1 or count > 10000:
        raise ValueError("Response count must be between 1 and 10000.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            page.goto(form_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            blocks = get_question_blocks(page)
            fields = []

            for block in blocks:
                q = re.sub(r"\s+", " ", question_text(block)).strip()
                if not q:
                    continue
                options = visible_option_labels(block)
                if block.locator('[role="checkbox"]').count():
                    qtype = "Checkbox"
                elif options:
                    qtype = "Choice"
                elif block.locator("textarea").count():
                    qtype = "Long Text"
                elif block.locator('input[type="email"]').count():
                    qtype = "Email"
                elif block.locator('input[type="date"]').count():
                    qtype = "Date"
                elif block.locator('input[type="number"]').count():
                    qtype = "Number"
                else:
                    qtype = "Text"
                fields.append({"question": q, "type": qtype, "options": options})

            if not fields:
                raise ValueError("No Google Form questions were detected.")

            dataset=[]
            for n in range(1, count+1):
                record={}
                for f in fields:
                    q=f["question"]; opts=f["options"]; typ=f["type"]
                    if typ == "Checkbox" and opts:
                        amount=random.randint(1, min(3,len(opts)))
                        value=random.sample(opts, amount)
                    elif opts:
                        value=random.choice(opts)
                    elif typ == "Email":
                        value=f"test{n}@example.com"
                    elif typ == "Date":
                        value="2026-12-15"
                    elif typ == "Number":
                        value=str(18+(n%43))
                    else:
                        value=_synthetic_text(q,n)
                    record[q]=value
                dataset.append(record)
            return dataset, fields
        finally:
            browser.close()


@app.post("/api/generate")
def api_generate():
    payload=request.get_json(silent=True) or {}
    form_url=str(payload.get("formUrl", "")).strip()
    try:
        count=int(payload.get("count", 10))
    except (TypeError, ValueError):
        return jsonify({"error":"Response count must be a whole number."}),400

    if not ("docs.google.com/forms" in form_url or "forms.gle/" in form_url):
        return jsonify({"error":"Please provide a valid Google Forms URL."}),400

    try:
        dataset, fields=generate_synthetic_dataset(form_url,count)
        return jsonify({
            "dataset":dataset,
            "count":len(dataset),
            "fieldCount":len(fields),
            "filename":"formpilot_synthetic_responses.json"
        })
    except Exception as exc:
        return jsonify({"error":str(exc)}),500


# ============================================================
# API: HOME
# ============================================================

@app.get("/")
def index():

    return send_from_directory(
        APP_DIR,
        "index.html"
    )


# ============================================================
# API: INSPECT FORM
# ============================================================

@app.post("/api/inspect")
def api_inspect():

    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    form_url = str(
        payload.get(
            "formUrl",
            ""
        )
    ).strip()

    if (
        "docs.google.com/forms"
        not in form_url
    ):

        return jsonify({
            "error":
            "Please provide a Google Forms URL."
        }), 400

    try:

        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=False
            )

            page = browser.new_page(
                viewport={
                    "width": 1280,
                    "height": 900
                }
            )

            try:

                page.goto(
                    form_url,
                    wait_until="domcontentloaded"
                )

                page.wait_for_timeout(
                    1500
                )

                blocks = get_question_blocks(
                    page
                )

                fields = []

                for i, block in enumerate(
                    blocks,
                    1
                ):

                    text = re.sub(
                        r"\s+",
                        " ",
                        question_text(
                            block
                        )
                    ).strip()

                    if not text:
                        continue

                    key = (
                        f"Q{i}_"
                        + re.sub(
                            r"[^A-Za-z0-9]+",
                            "_",
                            text
                        ).strip("_")[:60]
                    )

                    options = (
                        visible_option_labels(
                            block
                        )
                    )

                    if options:

                        qtype = "Choice"

                    elif block.locator(
                        "textarea"
                    ).count():

                        qtype = "Long Text"

                    else:

                        qtype = "Text"

                    fields.append({
                        "question": text,
                        "field": key,
                        "type": qtype,
                        "status": "Detected"
                    })

                return jsonify(
                    fields
                )

            finally:

                browser.close()

    except Exception as exc:

        return jsonify({
            "error": str(exc)
        }), 500


# ============================================================
# API: START
# ============================================================

@app.post("/api/start")
def api_start():

    global worker_thread

    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    # IMPORTANT:
    # Everything comes from the UI.
    # No hardcoded form URL.
    # No hardcoded JSON file.

    form_url = str(
        payload.get(
            "formUrl",
            ""
        )
    ).strip()

    mode = str(
        payload.get(
            "mode",
            "test"
        )
    ).lower()

    dataset = payload.get(
        "dataset"
    )

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if (
        "docs.google.com/forms"
        not in form_url
    ):

        return jsonify({
            "error":
            "Invalid Google Forms URL."
        }), 400

    if mode not in {
        "test",
        "submit"
    }:

        return jsonify({
            "error":
            "Invalid automation mode."
        }), 400

    if (
        not isinstance(
            dataset,
            list
        )
        or not dataset
    ):

        return jsonify({
            "error":
            "Upload a non-empty JSON dataset first."
        }), 400

    # --------------------------------------------------------
    # CHECK RUNNING
    # --------------------------------------------------------

    with state_lock:

        if (
            worker_thread
            and worker_thread.is_alive()
        ):

            return jsonify({
                "error":
                "Automation is already running."
            }), 409

        state.update({
            "status": "Starting",
            "current": 0,
            "total": len(dataset),
            "progress": 0,
            "logs": [],
            "error": None,
            "mode": mode
        })

    # --------------------------------------------------------
    # START WORKER
    # --------------------------------------------------------

    stop_event.clear()

    worker_thread = Thread(
        target=run_automation,
        args=(
            form_url,
            dataset,
            mode
        ),
        daemon=True
    )

    worker_thread.start()

    return jsonify({
        "status": "started",
        "message":
        "Python Playwright automation started."
    })


# ============================================================
# API: STOP
# ============================================================

@app.post("/api/stop")
def api_stop():

    stop_event.set()

    log(
        "Stop requested by operator."
    )

    return jsonify({
        "status": "stopping",
        "message":
        "Stop requested. "
        "The current operation will finish safely."
    })


# ============================================================
# API: STATUS
# ============================================================

@app.get("/api/status")
def api_status():

    with state_lock:

        return jsonify(
            dict(state)
        )


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    print(
        "========================================"
    )

    print(
        "        FORM PILOT"
    )

    print(
        "========================================"
    )

    print(
        "Open: http://127.0.0.1:5000"
    )

    print(
        "========================================"
    )

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False
    )