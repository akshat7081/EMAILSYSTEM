# ============================================================
# PYTHONANYWHERE WEBHOOK BOT v2.0 — bridge_app.py
# Full-featured Telegram email bot with:
#   ✅ 2-step flow: Pick Template → Send Instantly / Schedule
#   ✅ Clean email templates (no company/employee names)
#   ✅ Auto 4-day follow-up system
#   ✅ Smart email validation
#   ✅ Full queue management commands
#   ✅ Daily summary notifications
# ============================================================

import os, sys, json, re, hashlib, time, smtplib, logging, io
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from flask import Flask, request, jsonify

# ─── ENV ────────────────────────────────────────────────────
USERNAME = os.environ.get("PA_USERNAME", "akshat7081")
BASE_DIR = f"/home/{USERNAME}"

env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID     = os.environ.get("CHAT_ID", "")
GMAIL_EMAIL = os.environ.get("GMAIL_EMAIL", "")
GMAIL_PASS  = os.environ.get("GMAIL_APP_PASSWORD", "")
SECRET      = os.environ.get("MAIL_BOT_SECRET", "akshat123")

YOUR_NAME   = os.environ.get("YOUR_NAME", "Akshat Tripathi")
PHONE       = os.environ.get("PHONE", "+91-7081484808")
UNIVERSITY  = os.environ.get("UNIVERSITY", "Guru Gobind Singh Indraprastha University, New Delhi")
DEGREE      = os.environ.get("DEGREE", "BCA")
LINKEDIN    = os.environ.get("LINKEDIN", "linkedin.com/in/akshattripathi7081")
GITHUB      = os.environ.get("GITHUB", "github.com/akshat7081")

QUEUE_FILE   = os.path.join(BASE_DIR, "mail_queue.json")
SENT_LOG     = os.path.join(BASE_DIR, "sent_log.txt")
RESUME_FILE  = os.path.join(BASE_DIR, "resume.pdf")
DATA_DIR     = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

PA_URL = f"https://{USERNAME}.pythonanywhere.com"

# Follow-up configuration
FOLLOWUP_AFTER_DAYS = 4  # Auto follow-up after 4 days of no reply

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bridge")

# ─── Telegram API helpers ───────────────────────────────────
import urllib.request, urllib.parse

def tg_api(method, data=None):
    """Call Telegram Bot API."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    if data:
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"TG API error ({method}): {e}")
        return {}

def send_message(chat_id, text, parse_mode="Markdown", reply_markup=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_api("sendMessage", data)

def answer_callback(callback_id, text=""):
    return tg_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})

def edit_message(chat_id, message_id, text, parse_mode="Markdown", reply_markup=None):
    data = {"chat_id": chat_id, "message_id": message_id,
            "text": text, "parse_mode": parse_mode}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_api("editMessageText", data)

# ─── Email Extraction & Validation ──────────────────────────

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

BLACKLIST_DOMAINS = {
    "example.com", "test.com", "email.com", "domain.com",
    "yourcompany.com", "company.com", "abc.com", "xyz.com",
    "sentry.io", "github.com", "gitlab.com",
}

# Spammy TLDs to reject
SPAMMY_TLDS = {
    ".xyz", ".top", ".buzz", ".click", ".link",
    ".gq", ".ml", ".cf", ".tk", ".ga",
    ".work", ".icu", ".fun",
}

# Free email providers — allowed but flagged
FREE_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "rediffmail.com", "aol.com", "protonmail.com", "ymail.com",
    "live.com", "icloud.com", "mail.com",
}

def validate_email_quality(email):
    """
    Returns (is_valid, quality_flag, reason)
    quality_flag: 'good' | 'free' | 'blocked'
    """
    email = email.lower().strip()
    if not EMAIL_RE.match(email):
        return False, "blocked", "Invalid format"

    domain = email.split("@")[1] if "@" in email else ""

    if domain in BLACKLIST_DOMAINS:
        return False, "blocked", "Blacklisted domain"

    # Check spammy TLDs
    for tld in SPAMMY_TLDS:
        if domain.endswith(tld):
            return False, "blocked", f"Spammy TLD ({tld})"

    # Check image-like emails
    if email.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg")):
        return False, "blocked", "Image extension"

    # Flag free email domains
    if domain in FREE_DOMAINS:
        return True, "free", f"Free email ({domain})"

    return True, "good", "Corporate/valid"


def extract_emails(text):
    """Extract and validate emails from text."""
    raw = EMAIL_RE.findall(text or "")
    valid = []
    seen = set()
    for e in raw:
        e = e.lower().strip().rstrip(".")
        if e in seen:
            continue
        seen.add(e)
        is_valid, quality, reason = validate_email_quality(e)
        if is_valid:
            valid.append(e)
    return valid


def is_valid_email(email):
    return bool(EMAIL_RE.match(email or ""))

# ─── Queue Management ──────────────────────────────────────

def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_queue(data):
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def is_already_processed(email):
    """Check queue + sent log."""
    email = email.lower().strip()
    queue = load_queue()
    for item in queue:
        if item.get("email", "").lower().strip() == email:
            return True, item.get("status", "queued")
    # Check sent log
    if os.path.exists(SENT_LOG):
        try:
            with open(SENT_LOG, "r") as f:
                for line in f:
                    if line.strip().lower() == email:
                        return True, "sent"
        except:
            pass
    return False, None

def add_to_queue(email, company, role, template="normal", status="pending"):
    email = email.lower().strip()
    queue = load_queue()
    queue.append({
        "id": hashlib.md5(f"{email}{time.time()}".encode()).hexdigest()[:8],
        "email": email,
        "company": company or "Unknown",
        "role": role or "Entry-Level Opportunity",
        "template": template,
        "status": status,
        "attempts": 0,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "error": None,
        "followup_sent": False,
        "response_received": False,
    })
    save_queue(queue)

# ─── Job Detail Extraction ─────────────────────────────────

def extract_job_details(text):
    """Extract company name and role from job post text."""
    companies = []
    roles = []

    # Company patterns
    comp_patterns = [
        r"(?:company|organization|firm|employer)\s*[:]\s*(.+?)[\n\r,]",
        r"(?:hiring\s+(?:at|for|by))\s+(.+?)[\n\r,.]",
        r"(?:at|@)\s+([A-Z][a-zA-Z\s&]+(?:Ltd|Inc|Corp|Pvt|Technologies|Solutions|Services|Tech|Software|Systems|Digital|Group|Labs|Studio|Media|Networks|Consulting))",
    ]
    for pat in comp_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            c = m.group(1).strip()[:50]
            if len(c) > 2:
                companies.append(c)
                break

    # Role patterns
    role_patterns = [
        r"(?:position|role|job\s*title|designation|opening)\s*[:]\s*(.+?)[\n\r,]",
        r"(?:hiring|looking\s+for|opening\s+for)\s+(?:a\s+)?(.+?)[\n\r,.]",
        r"((?:senior|junior|lead|associate|intern|trainee|executive|manager|analyst|developer|engineer|designer|architect)\s+[\w\s]{3,30})",
    ]
    for pat in role_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            r = m.group(1).strip()[:50]
            if len(r) > 2:
                roles.append(r)
                break

    return {
        "companies": companies or ["Unknown"],
        "roles": roles or ["Entry-Level Opportunity"],
        "emails": extract_emails(text)
    }

# ─── Email Templates v2.0 (Cleaner, No Company Names) ──────

TEMPLATES = {
    "normal": {
        "name": "General IT / All Roles",
        "emoji": "💻",
        "description": "Python, SQL, HTML, Excel — general application",
    },
    "research": {
        "name": "Research Associate",
        "emoji": "🔬",
        "description": "Research, data collection, documentation",
    },
    "analytics": {
        "name": "Data Analytics",
        "emoji": "📊",
        "description": "Data Analyst, MIS, BI roles",
    },
    "followup": {
        "name": "Follow-Up Email",
        "emoji": "🔄",
        "description": "Polite follow-up after no response",
    },
}

def _signature_block():
    """Professional signature block with contact links."""
    return (
        f"Regards,\n"
        f"{YOUR_NAME}\n"
        f"{PHONE}\n"
        f"LinkedIn: {LINKEDIN}\n"
        f"GitHub: {GITHUB}"
    )


def get_email_content(template_id, to_email=""):
    """Generate professional email content — no company/employee names injected."""
    greeting = "Hello Ma'am/Sir,"
    name = YOUR_NAME
    degree = DEGREE
    university = UNIVERSITY
    sig = _signature_block()

    if template_id == "research":
        subject = f"Application for Research Associate Position — {name}"
        body = f"""{greeting}

I hope this email finds you well.

I am writing to express my interest in the Research Associate / Research Assistant position at your organization. I am a {degree} graduate from {university}, eager to contribute to impactful research-driven work.

My key competencies include:

  • Data collection, cleaning, and analysis using Python, Excel, and SQL
  • Literature review and academic research methodology
  • Statistical analysis and data interpretation
  • Technical documentation and research report writing
  • Survey design, data gathering, and synthesis
  • MS Office proficiency (Word, Excel, PowerPoint)
  • Database management (MySQL, data modelling)
  • Internet research and information compilation

I am detail-oriented, analytically strong, and capable of working both independently and collaboratively. I am passionate about contributing meaningfully to research initiatives.

I am available to join immediately with no notice period. My resume is attached for your reference. I would greatly appreciate the opportunity to discuss how my skills can support your research objectives.

Thank you for your time and consideration.

{sig}"""

    elif template_id == "analytics":
        subject = f"Application for Data Analyst Position — {name}"
        body = f"""{greeting}

I hope this email finds you well.

I am writing to apply for data analytics opportunities at your organization. I am a {degree} graduate from {university}, with a strong foundation in transforming raw data into actionable business insights.

My technical skills include:

  • Python for data analysis (Pandas, NumPy, Matplotlib, Seaborn)
  • SQL (complex queries, joins, aggregations, window functions, CTEs)
  • Advanced MS Excel (Pivot Tables, VLOOKUP, Power Query, dashboards)
  • Data cleaning, preprocessing, and transformation pipelines
  • Data visualization and business reporting
  • Statistical analysis and trend identification
  • MIS reporting and business intelligence fundamentals
  • Database management (MySQL, data modelling)

I have worked on projects involving sales data analysis, customer segmentation, automated reporting dashboards, and ETL pipelines.

I am open to roles such as Data Analyst, Business Analyst, MIS Analyst, Junior BI Developer, Analytics Executive, or any data-focused position.

I am available to join immediately with no notice period. My resume is attached for your review. I would welcome the opportunity to bring data-driven insights to your team.

Thank you for your time and consideration.

{sig}"""

    elif template_id == "followup":
        subject = f"Following Up — Job Application — {name}"
        body = f"""{greeting}

I hope you are doing well.

I am writing to politely follow up on my previous application that I had sent a few days ago. I remain genuinely interested in contributing to your team and wanted to reaffirm my enthusiasm for the opportunity.

I am a {degree} graduate with hands-on skills in Python, SQL, HTML/CSS, Excel, data analysis, and IT support. I am a quick learner who is eager to contribute and grow.

I am available to join immediately and can attend interviews at your convenience.

If my application was received, I would be grateful for any update regarding the next steps. If it was missed, I have re-attached my resume for your reference.

Apologies for any inconvenience, and thank you for your valuable time.

{sig}"""

    else:  # normal — general IT
        subject = f"Application for Entry-Level Opportunity — {name}"
        body = f"""{greeting}

I hope this email finds you well.

I am writing to express my interest in any suitable entry-level opportunity at your organization. I am a {degree} graduate from {university}, eager to launch my professional career and make meaningful contributions.

I have hands-on experience and skills in the following areas:

  • Python (scripting, automation, and data processing)
  • SQL (queries, joins, data handling, and reporting)
  • HTML5 and CSS3 (responsive web design)
  • MS Excel (VLOOKUP, Pivot Tables, data analysis, dashboards)
  • Data cleaning, analysis, and business problem-solving
  • IT support, troubleshooting, and system setup
  • Research, documentation, and report writing

I am open to roles such as Research Associate, Data Analyst, Junior Developer, Web Developer, IT Support Executive, MIS Executive, QA Tester, or any other entry-level position where I can contribute and grow.

I am available to join immediately with no notice period. My resume is attached for your review. I would sincerely appreciate the opportunity to discuss how my skills align with your requirements.

Thank you for your time and consideration.

{sig}"""

    return subject, body


# ─── SMTP Send ──────────────────────────────────────────────

def send_email_now(to_email, template_id="normal"):
    """Send email immediately via Gmail SMTP."""
    if not GMAIL_EMAIL or not GMAIL_PASS:
        return False, "Gmail credentials not set"

    subject, body = get_email_content(template_id, to_email)

    try:
        msg = MIMEMultipart()
        msg["From"] = f"{YOUR_NAME} <{GMAIL_EMAIL}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        if os.path.exists(RESUME_FILE):
            with open(RESUME_FILE, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition",
                                'attachment; filename="resume.pdf"')
                msg.attach(part)

        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
        server.starttls()
        server.login(GMAIL_EMAIL, GMAIL_PASS)
        server.sendmail(GMAIL_EMAIL, to_email, msg.as_string())
        server.quit()

        # Log it
        with open(SENT_LOG, "a") as f:
            f.write(f"{to_email}\n")

        return True, "Success"
    except Exception as e:
        logger.error(f"SMTP error: {e}")
        return False, str(e)


# ─── OCR (pytesseract on PA) ───────────────────────────────

def ocr_from_bytes(img_bytes):
    """Extract text from image bytes using pytesseract."""
    try:
        from PIL import Image
        import pytesseract
        img = Image.open(io.BytesIO(img_bytes))
        text = pytesseract.image_to_string(img)
        return text.strip() if text else ""
    except Exception as e:
        logger.error(f"OCR error: {e}")
        return ""


def download_tg_file(file_id):
    """Download a file from Telegram."""
    res = tg_api("getFile", {"file_id": file_id})
    if not res.get("ok"):
        return None
    file_path = res["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    try:
        resp = urllib.request.urlopen(url, timeout=30)
        return resp.read()
    except:
        return None


# ─── Pending data store (file-backed for PA WSGI) ──────────
PENDING_FILE = os.path.join(DATA_DIR, "pending_actions.json")

def load_pending():
    if os.path.exists(PENDING_FILE):
        try:
            with open(PENDING_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_pending(data):
    with open(PENDING_FILE, "w") as f:
        json.dump(data, f, indent=2)


def escape_md(text):
    """Escape Markdown special characters."""
    if not text:
        return ""
    for ch in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = str(text).replace(ch, f"\\{ch}")
    return text


# ═══════════════════════════════════════════════════════════
#  MESSAGE HANDLERS
# ═══════════════════════════════════════════════════════════

def handle_text(chat_id, text, message_id):
    """Handle incoming text message — extract emails, show template picker (Step 1)."""
    emails = extract_emails(text)

    if not emails:
        return

    # Check duplicates and quality
    valid = []
    free_domain_flags = []
    for e in emails:
        already, status = is_already_processed(e)
        if already:
            continue
        is_ok, quality, reason = validate_email_quality(e)
        if is_ok:
            valid.append(e)
            if quality == "free":
                free_domain_flags.append(f"  ⚠️ {e} ({reason})")

    if not valid:
        status_lines = []
        for e in list(set(emails)):
            _, st = is_already_processed(e)
            if st == "sent":
                status_lines.append(f"  ✅ {e} (Already sent)")
            elif st in ("queued", "pending"):
                status_lines.append(f"  🕒 {e} (In queue)")
            else:
                status_lines.append(f"  ⚙️ {e} (Processed)")
        send_message(chat_id,
                     "ℹ️ *Duplicate Detected:*\n\n" + "\n".join(status_lines))
        return

    details = extract_job_details(text)
    company = details["companies"][0]
    role = details["roles"][0]

    # Store pending action
    data_key = hashlib.md5(f"{valid[0]}{time.time()}".encode()).hexdigest()[:6]
    pending = load_pending()
    pending[data_key] = {
        "emails": valid,
        "company": company,
        "role": role,
        "ts": time.time()
    }
    save_pending(pending)

    # ── STEP 1: Template Picker Only (no send buttons yet) ──
    buttons = []
    for tid, info in TEMPLATES.items():
        if tid == "followup":
            continue
        buttons.append([{
            "text": f"{info['emoji']} {info['name']}",
            "callback_data": f"pick_{tid}_{data_key}"
        }])

    buttons.append([{
        "text": "❌ Cancel — Don't Send",
        "callback_data": f"cancel_{data_key}"
    }])

    status_lines = []
    for e in list(set(emails)):
        if e in valid:
            status_lines.append(f"  🟢 {e} (New)")
        else:
            _, st = is_already_processed(e)
            status_lines.append(f"  ✅ {e} (Already {st})")

    # Free domain warnings
    warning = ""
    if free_domain_flags:
        warning = "\n\n⚠️ *Free Email Detected:*\n" + "\n".join(free_domain_flags)

    send_message(
        chat_id,
        f"🎯 *Job Post Detected!*\n"
        f"🏢 Company: *{company}*\n"
        f"💼 Role: *{role}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📧 Total Found: {len(set(emails))}\n"
        f"✅ New Ready: {len(valid)}\n\n"
        f"*Status:*\n" + "\n".join(status_lines) +
        warning + "\n\n"
        f"👇 *Step 1: Choose Email Template:*",
        reply_markup={"inline_keyboard": buttons}
    )


def handle_photo(chat_id, file_id, message_id, caption=""):
    """Handle photo: OCR → extract emails → show options."""
    img_bytes = download_tg_file(file_id)
    if not img_bytes:
        send_message(chat_id, "❌ Could not download image.")
        return

    text = ocr_from_bytes(img_bytes)
    if caption:
        text = (text or "") + "\n" + caption

    if not text or not text.strip():
        send_message(chat_id, "⚠️ Could not read any text from this image. Try a clearer screenshot.")
        return

    emails = extract_emails(text)
    if not emails:
        send_message(chat_id,
                     f"📝 *OCR Text Extracted:*\n```\n{text[:500]}\n```\n\n⚠️ No email addresses found in this image.")
        return

    handle_text(chat_id, text, message_id)


# ═══════════════════════════════════════════════════════════
#  CALLBACK HANDLER — 2-STEP FLOW
# ═══════════════════════════════════════════════════════════

def handle_callback(callback_query):
    """Handle inline button presses — 2-step flow."""
    cb_id = callback_query["id"]
    data = callback_query.get("data", "")
    chat_id = callback_query["message"]["chat"]["id"]
    msg_id = callback_query["message"]["message_id"]

    pending = load_pending()

    # ══════════════════════════════════════════════════════
    # STEP 1 CALLBACK: pick_{tid}_{key}
    #   User chose a template → now show send mode buttons
    # ══════════════════════════════════════════════════════
    if data.startswith("pick_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            answer_callback(cb_id, "Invalid callback")
            return
        tid = parts[1]
        key = parts[2]

        info = pending.get(key)
        if not info:
            answer_callback(cb_id, "⏳ Expired. Forward again.")
            return

        # Save template choice
        info["template"] = tid
        save_pending(pending)

        tname = TEMPLATES.get(tid, {}).get("name", tid)
        temoji = TEMPLATES.get(tid, {}).get("emoji", "📧")

        # Show Step 2: Send Mode
        mode_buttons = [[
            {"text": "🚀 Send Instantly", "callback_data": f"send_{key}"},
            {"text": "⏰ Schedule (8 AM)", "callback_data": f"queue_{key}"},
        ], [
            {"text": "🔙 Back to Templates", "callback_data": f"back_{key}"},
            {"text": "❌ Cancel", "callback_data": f"cancel_{key}"},
        ]]

        answer_callback(cb_id, f"✅ Template: {tname}")
        edit_message(chat_id, msg_id,
            f"📋 *Template Selected:* {temoji} {tname}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 Emails: {len(info['emails'])}\n"
            f"🏢 Company: *{info.get('company', 'Unknown')}*\n"
            f"💼 Role: *{info.get('role', 'N/A')}*\n\n"
            f"👇 *Step 2: Choose Send Mode:*",
            reply_markup={"inline_keyboard": mode_buttons}
        )

    # ══════════════════════════════════════════════════════
    # STEP 2a: send_{key} — SEND INSTANTLY
    # ══════════════════════════════════════════════════════
    elif data.startswith("send_"):
        key = data[5:]
        info = pending.get(key)
        if not info:
            answer_callback(cb_id, "⏳ Expired. Forward again.")
            return

        emails = info["emails"]
        company = info.get("company", "Unknown")
        role = info.get("role", "N/A")
        tid = info.get("template", "normal")
        tname = TEMPLATES.get(tid, {}).get("name", tid)

        sent = 0
        failed = 0
        results = []
        for em in emails:
            already, _ = is_already_processed(em)
            if already:
                results.append(f"  ⏭️ {em} (skipped — already processed)")
                continue
            ok, err = send_email_now(em, tid)
            if ok:
                sent += 1
                add_to_queue(em, company, role, tid, "sent")
                results.append(f"  ✅ {em}")
            else:
                failed += 1
                results.append(f"  ❌ {em} ({err[:30]})")

        pending.pop(key, None)
        save_pending(pending)

        answer_callback(cb_id, f"🚀 Done! Sent: {sent}")
        result_text = "\n".join(results) if results else "  No emails to send"
        edit_message(chat_id, msg_id,
            f"🚀 *Instantly Sent!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📬 Template: *{tname}*\n"
            f"✅ Sent: *{sent}* | ❌ Failed: *{failed}*\n\n"
            f"*Results:*\n{result_text}\n\n"
            f"📩 Follow-up will auto-send in {FOLLOWUP_AFTER_DAYS} days if no reply."
        )

    # ══════════════════════════════════════════════════════
    # STEP 2b: queue_{key} — SCHEDULE FOR 8 AM
    # ══════════════════════════════════════════════════════
    elif data.startswith("queue_"):
        key = data[6:]
        info = pending.get(key)
        if not info:
            answer_callback(cb_id, "⏳ Expired. Forward again.")
            return

        emails = info["emails"]
        company = info.get("company", "Unknown")
        role = info.get("role", "N/A")
        tid = info.get("template", "normal")
        tname = TEMPLATES.get(tid, {}).get("name", tid)

        queued = 0
        skipped = 0
        for em in emails:
            already, _ = is_already_processed(em)
            if already:
                skipped += 1
                continue
            add_to_queue(em, company, role, tid, "pending")
            queued += 1

        pending.pop(key, None)
        save_pending(pending)

        answer_callback(cb_id, f"⏰ Queued {queued} for 8 AM!")
        edit_message(chat_id, msg_id,
            f"⏰ *Scheduled for 8 AM IST!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📬 Template: *{tname}*\n"
            f"📥 Queued: *{queued}* emails\n"
            + (f"⏭️ Skipped: *{skipped}* (duplicates)\n" if skipped else "") +
            f"\nPA scheduled task will send at 8:00 AM.\n"
            f"📩 Auto follow-up in {FOLLOWUP_AFTER_DAYS} days if no reply."
        )

    # ══════════════════════════════════════════════════════
    # BACK: back_{key} — Go back to template picker
    # ══════════════════════════════════════════════════════
    elif data.startswith("back_"):
        key = data[5:]
        info = pending.get(key)
        if not info:
            answer_callback(cb_id, "⏳ Expired. Forward again.")
            return

        # Rebuild template buttons
        buttons = []
        for tid, tinfo in TEMPLATES.items():
            if tid == "followup":
                continue
            buttons.append([{
                "text": f"{tinfo['emoji']} {tinfo['name']}",
                "callback_data": f"pick_{tid}_{key}"
            }])
        buttons.append([{
            "text": "❌ Cancel — Don't Send",
            "callback_data": f"cancel_{key}"
        }])

        answer_callback(cb_id, "🔙 Back to templates")
        edit_message(chat_id, msg_id,
            f"🎯 *Choose Template:*\n"
            f"📧 Emails: {len(info['emails'])}\n\n"
            f"👇 *Step 1: Select email template:*",
            reply_markup={"inline_keyboard": buttons}
        )

    # ══════════════════════════════════════════════════════
    # CANCEL: cancel_{key}
    # ══════════════════════════════════════════════════════
    elif data.startswith("cancel_"):
        key = data[7:]
        pending.pop(key, None)
        save_pending(pending)

        answer_callback(cb_id, "❌ Cancelled")
        edit_message(chat_id, msg_id,
            "❌ *Cancelled.*\n\nNo emails were sent. Forward another job post whenever ready.")

    # ══════════════════════════════════════════════════════
    # CLEAR CONFIRMATION: clearyes / clearno
    # ══════════════════════════════════════════════════════
    elif data == "clearyes":
        queue = load_queue()
        removed = sum(1 for x in queue if x.get("status") == "pending")
        queue = [x for x in queue if x.get("status") != "pending"]
        save_queue(queue)
        answer_callback(cb_id, f"🗑️ Cleared {removed} pending!")
        edit_message(chat_id, msg_id,
            f"🗑️ *Queue Cleared!*\n\n"
            f"Removed *{removed}* pending emails.\n"
            f"Sent emails preserved for follow-up tracking.")

    elif data == "clearno":
        answer_callback(cb_id, "✅ Kept queue!")
        edit_message(chat_id, msg_id, "✅ *Queue kept intact.* Nothing was deleted.")

    # ══════════════════════════════════════════════════════
    # JOB SCANNER: jobok_{key} — Approve scanned job
    # ══════════════════════════════════════════════════════
    elif data.startswith("jobok_"):
        key = data[6:]
        info = pending.get(key)
        if not info:
            answer_callback(cb_id, "⏳ Expired. Run /scan again.")
            return

        # Show template picker (Step 1 of existing 2-step flow)
        buttons = []
        for tid, tinfo in TEMPLATES.items():
            if tid == "followup":
                continue
            buttons.append([{
                "text": f"{tinfo['emoji']} {tinfo['name']}",
                "callback_data": f"pick_{tid}_{key}"
            }])
        buttons.append([{
            "text": "❌ Cancel — Don't Send",
            "callback_data": f"cancel_{key}"
        }])

        email_list = "\n".join(f"  📧 `{e}`" for e in info['emails'])
        answer_callback(cb_id, "✅ Approved!")
        edit_message(chat_id, msg_id,
            f"✅ *Job Approved!*\n"
            f"🏢 {info.get('company', 'Unknown')}\n"
            f"💼 {info.get('role', 'N/A')}\n\n"
            f"{email_list}\n\n"
            f"👇 *Step 1: Choose Email Template:*",
            reply_markup={"inline_keyboard": buttons}
        )

    # ══════════════════════════════════════════════════════
    # JOB SCANNER: jobno_{key} — Skip scanned job
    # ══════════════════════════════════════════════════════
    elif data.startswith("jobno_"):
        key = data[6:]
        pending.pop(key, None)
        save_pending(pending)
        answer_callback(cb_id, "⏭️ Skipped")
        edit_message(chat_id, msg_id,
            "⏭️ *Skipped.* This job won't be shown again.")

    # ══════════════════════════════════════════════════════
    # MENU BUTTONS: menu_{action}
    # ══════════════════════════════════════════════════════
    elif data.startswith("menu_"):
        action = data[5:]
        answer_callback(cb_id, "✅")

        if action == "status":
            cmd_status(chat_id)
        elif action == "stats":
            cmd_stats(chat_id)
        elif action == "queue":
            cmd_queue(chat_id)
        elif action == "scan":
            cmd_scan(chat_id)
        elif action == "followups":
            cmd_followups(chat_id)
        elif action == "clear":
            cmd_clear(chat_id)
        elif action == "bulk":
            send_message(chat_id,
                "📨 *Bulk Send*\n\n"
                "Paste emails after /bulk:\n\n"
                "`/bulk hr@abc.com, jobs@xyz.com info@co.in`\n\n"
                "Supports comma, space, newline, or semicolon separated.")
        elif action == "preview":
            # Show all template previews as buttons
            btns = []
            for tid, tinfo in TEMPLATES.items():
                if tid == "followup":
                    continue
                btns.append([{"text": f"{tinfo['emoji']} Preview: {tinfo['name']}",
                              "callback_data": f"mprev_{tid}"}])
            btns.append([{"text": "🔙 Back to Menu", "callback_data": "menu_home"}])
            send_message(chat_id,
                "👁️ *Choose template to preview:*",
                reply_markup={"inline_keyboard": btns})
        elif action == "help":
            cmd_help(chat_id)
        elif action == "home":
            _send_main_menu(chat_id)

    # Preview from menu
    elif data.startswith("mprev_"):
        tid = data[6:]
        answer_callback(cb_id, "✅")
        cmd_preview(chat_id, [tid])

    else:
        answer_callback(cb_id, "Unknown action")


# ═══════════════════════════════════════════════════════════
#  BOT COMMANDS
# ═══════════════════════════════════════════════════════════

def handle_command(chat_id, text, message_id):
    """Route /commands to handlers."""
    parts = text.strip().split()
    cmd = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []

    if cmd == "/start":
        cmd_start(chat_id)
    elif cmd == "/help":
        cmd_help(chat_id)
    elif cmd == "/status":
        cmd_status(chat_id)
    elif cmd == "/queue":
        cmd_queue(chat_id)
    elif cmd == "/stats":
        cmd_stats(chat_id)
    elif cmd == "/followups":
        cmd_followups(chat_id)
    elif cmd == "/clear":
        cmd_clear(chat_id)
    elif cmd == "/cancel":
        cmd_cancel(chat_id, args)
    elif cmd == "/preview":
        cmd_preview(chat_id, args)
    elif cmd == "/scan":
        cmd_scan(chat_id, args)
    elif cmd == "/scanstatus":
        cmd_scanstatus(chat_id)
    elif cmd == "/bulk":
        # Everything after /bulk is email text
        bulk_text = text[len("/bulk"):].strip()
        cmd_bulk(chat_id, bulk_text)
    else:
        send_message(chat_id, f"❓ Unknown command: `{cmd}`\n\nType /help for all commands.")


def _send_main_menu(chat_id):
    """Send the interactive main menu with buttons."""
    menu = [
        [{"text": "📊 Status", "callback_data": "menu_status"},
         {"text": "📈 Stats", "callback_data": "menu_stats"}],
        [{"text": "📬 Queue", "callback_data": "menu_queue"},
         {"text": "📩 Follow-ups", "callback_data": "menu_followups"}],
        [{"text": "🔎 Scan Jobs", "callback_data": "menu_scan"},
         {"text": "📨 Bulk Send", "callback_data": "menu_bulk"}],
        [{"text": "👁️ Preview Templates", "callback_data": "menu_preview"},
         {"text": "🗑️ Clear Queue", "callback_data": "menu_clear"}],
        [{"text": "❓ Help & Commands", "callback_data": "menu_help"}],
    ]
    send_message(chat_id,
        "👋 *Email Bot v2.0*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📸 Send a *screenshot* of a job post\n"
        "📝 Or *forward/paste text* with emails\n\n"
        "✨ *Quick Actions:*",
        reply_markup={"inline_keyboard": menu}
    )


def cmd_start(chat_id):
    _send_main_menu(chat_id)


def cmd_help(chat_id):
    send_message(chat_id,
        "📖 *All Commands*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 *Status & Info:*\n"
        "  /status — Quick queue overview\n"
        "  /stats — Detailed send statistics\n"
        "  /queue — Full queue with all emails\n"
        "  /followups — Emails due for follow-up\n\n"
        "⚙️ *Queue Management:*\n"
        "  /clear — Clear all pending emails\n"
        "  /cancel `<email>` — Cancel specific email\n\n"
        "📧 *Email:*\n"
        "  /preview `<template>` — Preview template\n"
        "     Templates: `normal`, `research`, `analytics`\n\n"
        "📨 *Bulk Send:*\n"
        "  /bulk `email1 email2 ...` — Paste many emails\n"
        "  Supports comma, space, or newline separated\n\n"
        "🔎 *Job Scanner:*\n"
        "  /scan — Scan LinkedIn/Indeed for jobs with HR emails\n"
        "  /scan `<query>` — Custom search (e.g. /scan python developer)\n"
        "  /scanstatus — Last scan results\n\n"
        "💡 *How to Use:*\n"
        "  1️⃣ Forward a job post or send a screenshot\n"
        "  2️⃣ Choose a template (Step 1)\n"
        "  3️⃣ Send instantly or schedule for 8 AM (Step 2)\n"
        "  4️⃣ Bot auto-follows up after 4 days! 🔄"
    )


def cmd_status(chat_id):
    queue = load_queue()
    pending = [x for x in queue if x.get("status") == "pending"]
    sent = [x for x in queue if x.get("status") == "sent"]
    failed = [x for x in queue if x.get("status") in ("failed", "permanently_failed")]
    bounced = [x for x in queue if x.get("status") == "bounced"]
    followup_due = []
    followup_sent = [x for x in queue if x.get("followup_sent")]
    replied = [x for x in queue if x.get("response_received")]

    now = datetime.now()
    for item in sent:
        if item.get("followup_sent") or item.get("response_received"):
            continue
        sent_date_str = item.get("updated_at") or item.get("added_at", "")
        if sent_date_str:
            try:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        sd = datetime.strptime(sent_date_str.strip(), fmt)
                        break
                    except ValueError:
                        continue
                else:
                    continue
                if (now - sd).days >= FOLLOWUP_AFTER_DAYS:
                    followup_due.append(item)
            except:
                pass

    send_message(chat_id,
        "📊 *Queue Status*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ Pending: *{len(pending)}*\n"
        f"✅ Sent: *{len(sent)}*\n"
        f"❌ Failed: *{len(failed)}*\n"
        f"📭 Bounced: *{len(bounced)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔄 Follow-ups Sent: *{len(followup_sent)}*\n"
        f"📩 Follow-ups Due: *{len(followup_due)}*\n"
        f"💬 Replies Received: *{len(replied)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📬 Total in Queue: *{len(queue)}*"
    )


def cmd_queue(chat_id):
    queue = load_queue()
    if not queue:
        send_message(chat_id, "📭 *Queue is empty!*\n\nForward a job post to get started.")
        return

    # Show recent 15 items
    lines = []
    status_emoji = {
        "pending": "⏳", "sent": "✅", "failed": "❌",
        "bounced": "📭", "sending": "📤",
        "permanently_failed": "💀",
    }
    for item in queue[-15:]:
        st = item.get("status", "?")
        emoji = status_emoji.get(st, "❓")
        email = item.get("email", "?")
        tmpl = item.get("template", "normal")
        date = item.get("added_at", "?")
        fu = " 🔄" if item.get("followup_sent") else ""
        rp = " 💬" if item.get("response_received") else ""
        lines.append(f"{emoji} `{email}`\n     📋 {tmpl} | 📅 {date}{fu}{rp}")

    msg = (
        f"📬 *Email Queue* (last {min(15, len(queue))} of {len(queue)})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n\n".join(lines) +
        "\n\n_🔄 = follow-up sent | 💬 = reply received_"
    )
    send_message(chat_id, msg)


def cmd_stats(chat_id):
    queue = load_queue()
    total = len(queue)
    sent = sum(1 for x in queue if x.get("status") == "sent")
    failed = sum(1 for x in queue if x.get("status") in ("failed", "permanently_failed"))
    pending = sum(1 for x in queue if x.get("status") == "pending")
    bounced = sum(1 for x in queue if x.get("status") == "bounced")
    followups = sum(1 for x in queue if x.get("followup_sent"))
    replies = sum(1 for x in queue if x.get("response_received"))

    success_rate = f"{(sent / total * 100):.1f}" if total > 0 else "0"

    # Count by template
    template_counts = {}
    for item in queue:
        t = item.get("template", "normal")
        template_counts[t] = template_counts.get(t, 0) + 1

    tmpl_lines = []
    for t, c in sorted(template_counts.items(), key=lambda x: -x[1]):
        emoji = TEMPLATES.get(t, {}).get("emoji", "📧")
        name = TEMPLATES.get(t, {}).get("name", t)
        tmpl_lines.append(f"  {emoji} {name}: *{c}*")

    # Today's activity
    today = datetime.now().strftime("%Y-%m-%d")
    sent_today = sum(1 for x in queue
                     if x.get("status") == "sent" and
                     (x.get("updated_at", "") or "").startswith(today))

    send_message(chat_id,
        "📈 *Email Statistics*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📬 Total Emails: *{total}*\n"
        f"✅ Successfully Sent: *{sent}*\n"
        f"❌ Failed: *{failed}*\n"
        f"📭 Bounced: *{bounced}*\n"
        f"⏳ Pending: *{pending}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Success Rate: *{success_rate}%*\n"
        f"📅 Sent Today: *{sent_today}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔄 Follow-ups Sent: *{followups}*\n"
        f"💬 Replies Received: *{replies}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 *By Template:*\n" + "\n".join(tmpl_lines)
    )


def cmd_followups(chat_id):
    queue = load_queue()
    now = datetime.now()

    due = []
    sent_already = []
    for item in queue:
        if item.get("status") != "sent":
            continue
        if item.get("response_received"):
            continue

        sent_date_str = item.get("updated_at") or item.get("added_at", "")
        if not sent_date_str:
            continue

        try:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    sd = datetime.strptime(sent_date_str.strip(), fmt)
                    break
                except ValueError:
                    continue
            else:
                continue
            days = (now - sd).days
        except:
            continue

        if item.get("followup_sent"):
            sent_already.append((item, days))
        elif days >= FOLLOWUP_AFTER_DAYS:
            due.append((item, days))

    if not due and not sent_already:
        send_message(chat_id,
            "📩 *Follow-Up Tracker*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "No follow-ups due or sent yet.\n"
            f"Follow-ups trigger after {FOLLOWUP_AFTER_DAYS} days of no reply.")
        return

    lines = []
    if due:
        lines.append(f"⏰ *Due for Follow-Up ({len(due)}):*")
        for item, days in due[:10]:
            lines.append(f"  📧 `{item['email']}` — {days} days ago")

    if sent_already:
        lines.append(f"\n✅ *Follow-Ups Already Sent ({len(sent_already)}):*")
        for item, days in sent_already[:10]:
            fu_date = item.get("followup_date", "?")
            lines.append(f"  🔄 `{item['email']}` — sent {fu_date}")

    send_message(chat_id,
        "📩 *Follow-Up Tracker*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n" +
        "\n".join(lines)
    )


def cmd_clear(chat_id):
    queue = load_queue()
    pending_count = sum(1 for x in queue if x.get("status") == "pending")

    if pending_count == 0:
        send_message(chat_id, "✅ *No pending emails to clear!*\n\nQueue is already clean.")
        return

    buttons = [[
        {"text": f"🗑️ Yes, Clear {pending_count} Pending", "callback_data": "clearyes"},
        {"text": "❌ No, Keep Them", "callback_data": "clearno"},
    ]]

    send_message(chat_id,
        f"⚠️ *Clear Pending Queue?*\n\n"
        f"This will remove *{pending_count}* pending emails.\n"
        f"Already-sent emails will be preserved for follow-up tracking.\n\n"
        f"Are you sure?",
        reply_markup={"inline_keyboard": buttons}
    )


def cmd_cancel(chat_id, args):
    if not args:
        send_message(chat_id,
            "Usage: `/cancel email@example.com`\n\n"
            "This removes a specific email from the pending queue.")
        return

    target = args[0].lower().strip()
    queue = load_queue()
    found = False
    new_queue = []
    for item in queue:
        if item.get("email", "").lower() == target and item.get("status") == "pending":
            found = True
            continue
        new_queue.append(item)

    if found:
        save_queue(new_queue)
        send_message(chat_id, f"🗑️ Removed `{target}` from pending queue.")
    else:
        send_message(chat_id, f"❌ `{target}` not found in pending queue.\n\nUse /queue to see current emails.")


def cmd_preview(chat_id, args):
    """Preview an email template."""
    tid = args[0].lower() if args else "normal"
    if tid not in TEMPLATES or tid == "followup":
        available = [k for k in TEMPLATES if k != "followup"]
        send_message(chat_id,
            f"❌ Template `{tid}` not found.\n\n"
            f"Available: {', '.join(available)}\n"
            f"Example: `/preview research`")
        return

    subject, body = get_email_content(tid)
    tinfo = TEMPLATES[tid]

    # Truncate if too long for Telegram
    if len(body) > 2500:
        body = body[:2500] + "\n\n... (truncated)"

    send_message(chat_id,
        f"👁️ *Template Preview: {tinfo['emoji']} {tinfo['name']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 *Subject:*\n`{subject}`\n\n"
        f"📝 *Body:*\n─────────────────────────\n"
        f"{body}\n"
        f"─────────────────────────\n\n"
        f"📄 Resume: attached automatically"
    )



def cmd_scan(chat_id, args=None):
    """Trigger LinkedIn/Indeed job scan."""
    import threading

    def _run_scan(custom_queries=None):
        try:
            # Import the scanner
            import importlib.util
            scanner_path = os.path.join(BASE_DIR, "job_scanner.py")
            if not os.path.exists(scanner_path):
                send_message(chat_id, "❌ `job_scanner.py` not found on server.")
                return
            spec = importlib.util.spec_from_file_location("job_scanner", scanner_path)
            scanner = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(scanner)

            if custom_queries:
                scanner.run_scan(queries=custom_queries)
            else:
                scanner.run_scan()
        except Exception as e:
            logger.error(f"Scan error: {e}")
            send_message(chat_id, f"❌ *Scan Failed:*\n`{str(e)[:200]}`")

    if args:
        # Custom search query: /scan python developer gurugram
        custom_query = " ".join(args)
        send_message(chat_id,
            f"🔎 *Custom Scan Starting...*\n"
            f"Query: `{custom_query}`\n\n"
            f"⏳ This may take 1-2 minutes. Results will appear here.")
        t = threading.Thread(target=_run_scan, args=([custom_query],), daemon=True)
    else:
        send_message(chat_id,
            "🔎 *Full Scan Starting...*\n\n"
            "📋 Searching: Data Analyst, Research Analyst, QA, MIS, etc.\n"
            "📍 Locations: Gurugram, Delhi, Noida, New Delhi\n"
            "📅 Filter: Last 15 days with HR emails\n\n"
            "⏳ This may take 3-5 minutes. I'll send each match as it's found!")
        t = threading.Thread(target=_run_scan, daemon=True)

    t.start()


def cmd_scanstatus(chat_id):
    """Show last scan results."""
    scan_log_file = os.path.join(DATA_DIR, "scan_log.json")
    if not os.path.exists(scan_log_file):
        send_message(chat_id, "ℹ️ No scan history yet. Run /scan to start.")
        return

    try:
        with open(scan_log_file, "r") as f:
            logs = json.load(f)
    except:
        send_message(chat_id, "ℹ️ No scan history yet. Run /scan to start.")
        return

    if not logs:
        send_message(chat_id, "ℹ️ No scan history yet. Run /scan to start.")
        return

    # Show last 5 scans
    lines = []
    for log in logs[-5:]:
        ts = log.get("timestamp", "?")[:16]
        found = log.get("total_found", 0)
        emails = log.get("with_emails", 0)
        calls = log.get("api_calls", 0)
        errors = log.get("errors", 0)
        lines.append(
            f"  📅 {ts}\n"
            f"     🔍 Found: {found} | 📧 With Emails: *{emails}* | API: {calls}"
            + (f" | ❌ Err: {errors}" if errors else "")
        )

    seen_file = os.path.join(DATA_DIR, "seen_jobs.json")
    seen_count = 0
    if os.path.exists(seen_file):
        try:
            with open(seen_file, "r") as f:
                seen_count = len(json.load(f))
        except:
            pass

    send_message(chat_id,
        "📊 *Scan History*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n" +
        "\n\n".join(lines) +
        f"\n\n👁️ Jobs in memory: *{seen_count}* (won't be shown again)"
    )


def cmd_bulk(chat_id, email_text):
    """
    Bulk email command: /bulk email1@x.com, email2@y.com ...
    Supports comma, space, newline, semicolon separated.
    Flows into the same 2-step template → send/schedule flow.
    """
    if not email_text:
        send_message(chat_id,
            "📨 *Bulk Send*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Paste emails after /bulk:\n\n"
            "```\n"
            "/bulk hr@abc.com, jobs@xyz.com\n"
            "info@company.in recruit@firm.co\n"
            "```\n\n"
            "Supports comma, space, newline, or semicolon separated.\n"
            "All emails get the same template and send mode.")
        return

    # Parse: support comma, semicolon, space, newline
    raw_text = email_text.replace(",", " ").replace(";", " ").replace("\n", " ")
    all_emails = extract_emails(raw_text)

    if not all_emails:
        send_message(chat_id,
            "❌ *No valid emails found!*\n\n"
            "Make sure you paste valid email addresses after /bulk.\n"
            "Example: `/bulk hr@abc.com, jobs@xyz.com`")
        return

    # Validate and classify
    valid = []
    skipped_dup = []
    skipped_bad = []
    free_flags = []

    for e in all_emails:
        # Check duplicate
        already, status = is_already_processed(e)
        if already:
            skipped_dup.append(f"  ⏭️ `{e}` ({status})")
            continue

        # Validate quality
        is_ok, quality, reason = validate_email_quality(e)
        if not is_ok:
            skipped_bad.append(f"  ❌ `{e}` ({reason})")
            continue

        valid.append(e)
        if quality == "free":
            free_flags.append(f"  ⚠️ `{e}` ({reason})")

    if not valid:
        lines = []
        if skipped_dup:
            lines.append("*Already Processed:*\n" + "\n".join(skipped_dup[:10]))
        if skipped_bad:
            lines.append("*Invalid:*\n" + "\n".join(skipped_bad[:10]))
        send_message(chat_id,
            "📨 *Bulk Send — No New Emails*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n" +
            "\n\n".join(lines))
        return

    # Store in pending
    data_key = hashlib.md5(f"bulk_{valid[0]}_{time.time()}".encode()).hexdigest()[:6]
    pending = load_pending()
    pending[data_key] = {
        "emails": valid,
        "company": "Bulk Send",
        "role": "Multiple Positions",
        "source": "bulk",
        "ts": time.time(),
    }
    save_pending(pending)

    # Show template picker (Step 1)
    buttons = []
    for tid, tinfo in TEMPLATES.items():
        if tid == "followup":
            continue
        buttons.append([{
            "text": f"{tinfo['emoji']} {tinfo['name']}",
            "callback_data": f"pick_{tid}_{data_key}"
        }])
    buttons.append([{
        "text": "❌ Cancel — Don't Send",
        "callback_data": f"cancel_{data_key}"
    }])

    # Build status message
    status_parts = [
        f"📨 *Bulk Send — {len(valid)} Emails Ready!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ New Emails: *{len(valid)}*\n"
    ]
    if skipped_dup:
        status_parts.append(f"⏭️ Duplicates Skipped: *{len(skipped_dup)}*\n")
    if skipped_bad:
        status_parts.append(f"❌ Invalid Skipped: *{len(skipped_bad)}*\n")

    # Show first 15 emails
    email_preview = "\n".join(f"  📧 `{e}`" for e in valid[:15])
    if len(valid) > 15:
        email_preview += f"\n  _... +{len(valid)-15} more_"

    status_parts.append(f"\n*Emails:*\n{email_preview}\n")

    if free_flags:
        status_parts.append("\n⚠️ *Free Domains Detected:*\n" + "\n".join(free_flags[:5]) + "\n")

    status_parts.append("\n👇 *Step 1: Choose Template for ALL emails:*")

    send_message(chat_id,
        "".join(status_parts),
        reply_markup={"inline_keyboard": buttons}
    )


# ═══════════════════════════════════════════════════════════
#  FLASK APP
# ═══════════════════════════════════════════════════════════

app = Flask(__name__)

@app.route("/")
def index():
    queue = load_queue()
    pending = sum(1 for x in queue if x.get("status") == "pending")
    sent = sum(1 for x in queue if x.get("status") == "sent")
    return jsonify({
        "status": "alive",
        "bot": "PA Email Bot v2.0",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "queue": {"pending": pending, "sent": sent, "total": len(queue)}
    })

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    """Handle incoming Telegram webhook updates."""
    try:
        update = request.get_json(force=True)
        if not update:
            return "ok", 200

        # Handle callback queries (button presses)
        if "callback_query" in update:
            handle_callback(update["callback_query"])
            return "ok", 200

        # Handle messages
        msg = update.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        if not chat_id:
            return "ok", 200

        # Only respond to authorized user
        if str(chat_id) != str(CHAT_ID):
            return "ok", 200

        text = msg.get("text", "")

        # Route commands
        if text.startswith("/"):
            handle_command(chat_id, text, msg.get("message_id", 0))
            return "ok", 200

        # Photo message
        if msg.get("photo"):
            photos = msg["photo"]
            file_id = photos[-1]["file_id"]
            caption = msg.get("caption", "")
            handle_photo(chat_id, file_id, msg.get("message_id", 0), caption)
            return "ok", 200

        # Document (image)
        doc = msg.get("document", {})
        if doc and doc.get("mime_type", "").startswith("image/"):
            handle_photo(chat_id, doc["file_id"], msg.get("message_id", 0),
                         msg.get("caption", ""))
            return "ok", 200

        # Text message (non-command)
        if text:
            handle_text(chat_id, text, msg.get("message_id", 0))
            return "ok", 200

    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)

    return "ok", 200


@app.route("/api/mail_queue", methods=["GET"])
def api_get_queue():
    """Serve queue for bot.py scheduled task."""
    secret = request.args.get("secret", "")
    if secret != SECRET:
        return "Forbidden", 403
    queue = load_queue()
    pending = [x for x in queue if x.get("status") == "pending"]
    return jsonify(pending)


@app.route("/api/mail_queue_sync", methods=["POST"])
def api_sync_queue():
    """Receive queue updates from bot.py."""
    secret = request.form.get("secret", "")
    if secret != SECRET:
        return "Forbidden", 403
    try:
        q = json.loads(request.form.get("queue", "[]"))
        save_queue(q)
        return "OK", 200
    except Exception as e:
        return str(e), 500


@app.route("/api/update_status", methods=["POST"])
def api_update_status():
    """Update email status (used by bot.py)."""
    secret = request.form.get("secret", "")
    if secret != SECRET:
        return "Forbidden", 403
    email = request.form.get("email", "").lower()
    status = request.form.get("status", "")
    if not email or not status:
        return "Missing params", 400

    queue = load_queue()
    for item in queue:
        if item.get("email", "").lower() == email:
            item["status"] = status
            item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_queue(queue)
    return "OK", 200


@app.route("/api/stats", methods=["GET"])
def api_stats():
    """JSON stats endpoint."""
    queue = load_queue()
    return jsonify({
        "total": len(queue),
        "pending": sum(1 for x in queue if x.get("status") == "pending"),
        "sent": sum(1 for x in queue if x.get("status") == "sent"),
        "failed": sum(1 for x in queue if x.get("status") in ("failed", "permanently_failed")),
        "bounced": sum(1 for x in queue if x.get("status") == "bounced"),
        "followups_sent": sum(1 for x in queue if x.get("followup_sent")),
        "replies_received": sum(1 for x in queue if x.get("response_received")),
    })


# Export for WSGI
application = app
