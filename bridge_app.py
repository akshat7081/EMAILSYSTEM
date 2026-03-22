# ============================================================
# PYTHONANYWHERE WEBHOOK BOT — bridge_app.py
# Self-contained Flask app that handles Telegram messages
# via webhook. No Replit needed!
# ============================================================

import os, sys, json, re, hashlib, time, smtplib, logging, io
from datetime import datetime
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

QUEUE_FILE   = os.path.join(BASE_DIR, "mail_queue.json")
SENT_LOG     = os.path.join(BASE_DIR, "sent_log.txt")
RESUME_FILE  = os.path.join(BASE_DIR, "resume.pdf")
DATA_DIR     = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

PA_URL = f"https://{USERNAME}.pythonanywhere.com"

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

# ─── Email Extraction ──────────────────────────────────────

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)
BLACKLIST_DOMAINS = {
    "example.com", "test.com", "email.com", "domain.com",
    "yourcompany.com", "company.com", "abc.com", "xyz.com",
    "sentry.io", "github.com", "gitlab.com",
}

def extract_emails(text):
    raw = EMAIL_RE.findall(text or "")
    valid = []
    seen = set()
    for e in raw:
        e = e.lower().strip().rstrip(".")
        if e in seen:
            continue
        seen.add(e)
        domain = e.split("@")[1] if "@" in e else ""
        if domain in BLACKLIST_DOMAINS:
            continue
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg")):
            continue
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
        "company": company or "Unknown Company",
        "role": role or "Entry-Level Opportunity",
        "template": template,
        "status": status,
        "attempts": 0,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "error": None
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

# ─── Email Templates (synced with mail.py) ──────────────────

TEMPLATES = {
    "normal":   {"name": "General IT / All Roles", "emoji": "💻"},
    "research": {"name": "Research Associate",     "emoji": "🔬"},
    "analytics":{"name": "Data Analytics",         "emoji": "📊"},
    "followup": {"name": "Follow-Up Email",        "emoji": "🔄"},
}

def get_email_content(template_id, to_email=""):
    greeting = "Hello Ma'am/Sir,"
    name = YOUR_NAME
    phone = PHONE
    degree = DEGREE
    university = UNIVERSITY

    if template_id == "research":
        subject = f"Application for Research Associate Position - {name}"
        body = f"""{greeting}

I hope you are doing well.

I am writing to express my interest in the Research Associate / Research Assistant position at your organization. I am a {degree} graduate from {university}, and I am keen to contribute to research-driven work.

I bring skills and experience in the following areas:

- Data collection, cleaning, and analysis (Python, Excel, SQL)
- Literature review and academic research methodology
- Statistical analysis and data interpretation
- Technical documentation and report writing
- Survey design, data gathering, and synthesis
- MS Office proficiency (Word, Excel, PowerPoint)
- Database management (MySQL, data modelling)
- Internet research and information compilation

I have strong analytical thinking, attention to detail, and the ability to work both independently and in a team. I am a quick learner and passionate about contributing to meaningful research.

I am an immediate joiner with no notice period. Please find my resume attached for your review. I would sincerely appreciate the opportunity to discuss how my skills can support the research goals at your organization.

Thank you for your time and consideration.

Regards,
{name}
{phone}"""

    elif template_id == "analytics":
        subject = f"Application for Data Analyst Position - {name}"
        body = f"""{greeting}

I hope you are doing well.

I am writing to apply for data analytics opportunities at your organization. I am a {degree} graduate from {university}, with a strong interest in turning data into actionable insights.

I bring hands-on skills in the following areas:

- Python for data analysis (Pandas, NumPy, Matplotlib)
- SQL (complex queries, joins, aggregations, window functions)
- Advanced MS Excel (Pivot Tables, VLOOKUP, Power Query, dashboards)
- Data cleaning, preprocessing, and transformation
- Data visualization and reporting
- Statistical analysis and trend identification
- MIS reporting and business intelligence basics
- Database management (MySQL, data modelling)

I have worked on projects involving sales data analysis, customer segmentation, automated reporting dashboards, and data cleaning pipelines.

I am open to roles such as Data Analyst, Business Analyst, MIS Analyst, Junior BI Developer, Analytics Executive, or any data-focused entry-level position.

I am an immediate joiner with no notice period. Please find my resume attached for your review. I would be grateful for the opportunity to contribute data-driven insights at your organization.

Thank you for your time and consideration.

Regards,
{name}
{phone}"""

    elif template_id == "followup":
        subject = f"Following Up - Job Application - {name}"
        body = f"""{greeting}

I hope you are doing well.

I am writing to follow up on my previous application for entry-level opportunities at your organization, which I had sent a few days ago.

I remain very interested in contributing to your team and wanted to reiterate my enthusiasm for the opportunity. I am a {degree} graduate with skills in Python, SQL, HTML/CSS, Excel, data analysis, and IT support.

I am an immediate joiner and available for interviews at your convenience.

If my application was received, I would be grateful for any update regarding the next steps. If not, I have re-attached my resume for your reference.

Apologies for any inconvenience, and thank you for your time.

Regards,
{name}
{phone}"""

    else:  # normal
        subject = f"Application for Entry-Level Opportunity - {name}"
        body = f"""{greeting}

I hope you are doing well.

I am writing to express my interest in any suitable entry-level opportunity available at your organization. I am a {degree} graduate from {university}, and I am eager to start my professional career.

I bring hands-on experience and skills in the following areas:

- Python (scripting and automation)
- SQL (queries, joins, and data handling)
- HTML5 and CSS3 (responsive web design)
- MS Excel (VLOOKUP, Pivot Tables, data analysis, and reporting)
- Data cleaning, data analysis, and business problem-solving
- IT support, troubleshooting, and system setup
- Research and documentation

I am open to roles such as Research Associate, Data Analyst, Junior Developer, Web Developer, IT Support Executive, MIS Executive, QA Tester, or any other entry-level position where I can contribute and grow.

I am an immediate joiner with no notice period. Please find my resume attached for your review. I would sincerely appreciate the opportunity to discuss how my skills align with your current requirements.

Thank you for your time and consideration.

Regards,
{name}
{phone}"""

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

        server = smtplib.SMTP("smtp.gmail.com", 587)
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


# ─── Pending data store (in-memory per process, file-backed) ─
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


# ─── MESSAGE HANDLERS ──────────────────────────────────────

def handle_text(chat_id, text, message_id):
    """Handle incoming text message — extract emails, show options."""
    emails = extract_emails(text)

    if not emails:
        # Not a job post with emails, just acknowledge
        return

    # Check duplicates
    valid = []
    for e in emails:
        already, status = is_already_processed(e)
        if not already:
            valid.append(e)

    if not valid:
        status_lines = []
        for e in list(set(emails)):
            _, st = is_already_processed(e)
            if st == "sent":
                status_lines.append(f"  ✅ {e} (Already sent)")
            elif st == "queued" or st == "pending":
                status_lines.append(f"  🕒 {e} (In queue)")
            else:
                status_lines.append(f"  ⚙️ {e} (Processed)")
        send_message(chat_id,
                     "ℹ️ *Duplicate Detected:*\n\n" + "\n".join(status_lines))
        return

    details = extract_job_details(text)
    company = details["companies"][0]
    role = details["roles"][0]

    # Store pending action (file-backed for PA WSGI)
    data_key = hashlib.md5(f"{valid[0]}{time.time()}".encode()).hexdigest()[:6]
    pending = load_pending()
    pending[data_key] = {
        "emails": valid,
        "company": company,
        "role": role,
        "ts": time.time()
    }
    save_pending(pending)

    # Build template picker buttons
    buttons = []
    for tid, info in TEMPLATES.items():
        if tid == "followup":
            continue
        buttons.append([{
            "text": f"{info['emoji']} {info['name']}",
            "callback_data": f"tmpl_{tid}_{data_key}"
        }])

    buttons.append([
        {"text": "🚀 Send Instantly (Normal)", "callback_data": f"israw_{data_key}"},
        {"text": "⏰ Schedule 8 AM", "callback_data": f"sched_{data_key}"}
    ])

    status_lines = []
    for e in list(set(emails)):
        if e in valid:
            status_lines.append(f"  🟢 {e} (New)")
        else:
            _, st = is_already_processed(e)
            status_lines.append(f"  ✅ {e} (Already {st})")

    send_message(
        chat_id,
        f"🎯 *Job Post Detected!*\n"
        f"🏢 Company: *{company}*\n"
        f"💼 Role: *{role}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📧 Total Found: {len(set(emails))}\n"
        f"✅ New Ready: {len(valid)}\n\n"
        f"*Status:*\n" + "\n".join(status_lines) + "\n\n"
        f"👇 *Select Template or Send Instantly:*",
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

    # Found emails — treat as text
    handle_text(chat_id, text, message_id)


def handle_callback(callback_query):
    """Handle inline button presses."""
    cb_id = callback_query["id"]
    data = callback_query.get("data", "")
    chat_id = callback_query["message"]["chat"]["id"]
    msg_id = callback_query["message"]["message_id"]

    pending = load_pending()

    # ── Template select: tmpl_{tid}_{key} ──
    if data.startswith("tmpl_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            answer_callback(cb_id, "Invalid callback")
            return
        tid = parts[1]
        key = parts[2]

        info = pending.get(key)
        if not info:
            answer_callback(cb_id, "⏳ Expired. Send again.")
            return

        emails = info["emails"]
        company = info["company"]
        role = info["role"]

        sent = 0
        failed = 0
        for email in emails:
            already, _ = is_already_processed(email)
            if already:
                continue
            ok, err = send_email_now(email, tid)
            if ok:
                sent += 1
                # Add to queue as sent for tracking
                add_to_queue(email, company, role, tid, "sent")
            else:
                failed += 1

        # Cleanup
        pending.pop(key, None)
        save_pending(pending)

        tname = TEMPLATES.get(tid, {}).get("name", tid)
        answer_callback(cb_id, f"✅ Sent {sent} emails!")
        edit_message(chat_id, msg_id,
                     f"✅ *Sent!*\n"
                     f"📬 Template: {tname}\n"
                     f"✅ Sent: {sent} | ❌ Failed: {failed}")

    # ── Instant Send Raw: israw_{key} ──
    elif data.startswith("israw_"):
        key = data[6:]
        info = pending.get(key)
        if not info:
            answer_callback(cb_id, "⏳ Expired. Send again.")
            return

        emails = info["emails"]
        company = info["company"]
        role = info["role"]

        sent = 0
        failed = 0
        for email in emails:
            already, _ = is_already_processed(email)
            if already:
                continue
            ok, err = send_email_now(email, "normal")
            if ok:
                sent += 1
                add_to_queue(email, company, role, "normal", "sent")
            else:
                failed += 1

        pending.pop(key, None)
        save_pending(pending)

        answer_callback(cb_id, f"🚀 Sent {sent} emails!")
        edit_message(chat_id, msg_id,
                     f"🚀 *Instantly Sent!*\n"
                     f"✅ Sent: {sent} | ❌ Failed: {failed}")

    # ── Schedule for 8 AM: sched_{key} ──
    elif data.startswith("sched_"):
        key = data[6:]
        info = pending.get(key)
        if not info:
            answer_callback(cb_id, "⏳ Expired. Send again.")
            return

        emails = info["emails"]
        company = info["company"]
        role = info["role"]

        queued = 0
        for email in emails:
            already, _ = is_already_processed(email)
            if already:
                continue
            add_to_queue(email, company, role, "normal", "pending")
            queued += 1

        pending.pop(key, None)
        save_pending(pending)

        answer_callback(cb_id, f"⏰ Queued {queued} for 8 AM!")
        edit_message(chat_id, msg_id,
                     f"⏰ *Scheduled for 8 AM!*\n"
                     f"📬 Queued: {queued} emails\n"
                     f"PA will send them at 8:00 AM IST.")
    else:
        answer_callback(cb_id, "Unknown action")


# ─── Flask App ──────────────────────────────────────────────

app = Flask(__name__)

@app.route("/")
def index():
    return jsonify({
        "status": "alive",
        "bot": "PA Webhook Bot",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

        # /start command
        text = msg.get("text", "")
        if text == "/start":
            send_message(chat_id,
                         "👋 *Bot is LIVE on PythonAnywhere!*\n\n"
                         "📸 Send a *screenshot* of a job post\n"
                         "📝 Or *forward/paste text* with email addresses\n\n"
                         "I'll extract emails and let you:\n"
                         "🚀 *Send Instantly*\n"
                         "⏰ *Schedule for 8 AM*\n"
                         "📋 *Pick a template*\n\n"
                         "No Replit needed — always on! ✅")
            return "ok", 200

        # /status command
        if text == "/status":
            queue = load_queue()
            pending = [x for x in queue if x.get("status") == "pending"]
            sent = [x for x in queue if x.get("status") == "sent"]
            send_message(chat_id,
                         f"📊 *Queue Status*\n"
                         f"⏳ Pending: {len(pending)}\n"
                         f"✅ Sent: {len(sent)}\n"
                         f"📬 Total: {len(queue)}")
            return "ok", 200

        # Photo message
        if msg.get("photo"):
            photos = msg["photo"]
            file_id = photos[-1]["file_id"]  # Highest res
            caption = msg.get("caption", "")
            handle_photo(chat_id, file_id, msg.get("message_id", 0), caption)
            return "ok", 200

        # Document (image)
        doc = msg.get("document", {})
        if doc and doc.get("mime_type", "").startswith("image/"):
            handle_photo(chat_id, doc["file_id"], msg.get("message_id", 0),
                         msg.get("caption", ""))
            return "ok", 200

        # Text message
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


# Export for WSGI
application = app
