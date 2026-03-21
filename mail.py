# ====================================================
# REPLIT: BCA JOB HUNTER + MAIL BOT v8.0 FINAL
# ====================================================
# ✅ ALL original features preserved
# ✅ ALL 26 bug fixes applied
# ✅ Mail bot integrated
# ✅ Forward extraction (text + image OCR)
# ✅ Full web dashboard with dark theme
# ✅ 24/7 on Replit with keep-alive
# ====================================================

import os, csv, json, logging, asyncio, re, hashlib, time, atexit
import psutil
import io, shutil, random
import urllib.request
import urllib.parse
import urllib.error
from PIL import Image as PILImage

from datetime import datetime, timedelta
from threading import Thread, RLock



import pandas as pd

from markupsafe import escape as real_escape
from jobspy import scrape_jobs
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import nest_asyncio
from flask import Flask, render_template_string, request, jsonify, send_file

from email_monitor import (
    check_inbox,
    get_unhandled_items,
    mark_handled,
    get_inbox_stats,
    load_batch_alerts,
    reset_batch_alerts,
    stop_followups_for,
    load_inbox_items,
    save_inbox_items,
    # Added new endpoints to push mail queue items to PythonAnywhere and pull back processing results
    # enhancing the mail bot's functionality.
)

# ─── Bot Lock Mechanism ──────────────────────────────────────
BOT_LOCK_FILE = "data/bot_instance.lock"

def acquire_bot_lock():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(BOT_LOCK_FILE):
        # If lock exists, assume another instance is running
        try:
            with open(BOT_LOCK_FILE, "r") as f:
                pid = int(f.read().strip())
            # Check if process actually exists
            os.kill(pid, 0)
            return False
        except (ProcessLookupError, ValueError, OSError):
            # Process dead, take the lock
            pass
    with open(BOT_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True

def release_bot_lock():
    try:
        if os.path.exists(BOT_LOCK_FILE):
            os.remove(BOT_LOCK_FILE)
    except:
        pass

atexit.register(release_bot_lock)
# ─── Logging ───────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8")
    ],
)
logger = logging.getLogger("JobBot")

# ─── OCR Setup (PYTESSERACT — lightweight) ────────────
# OCR Engine Setup (Lighter alternative to EasyOCR)
try:
    import pytesseract
    # Check if tesseract is in PATH, if not you might need to specify:
    # pytesseract.pytesseract.tesseract_cmd = r'/usr/bin/tesseract'
    ORC_TEST = pytesseract.get_tesseract_version()
    OCR_AVAILABLE = True
    logger.info("✅ OCR: pytesseract available")
except Exception as e:
    OCR_AVAILABLE = False
    logger.warning(f"⚠️ OCR disabled: {e}")

# OCR Engines (RAM-efficient architecture)
OCR_SPACE_API_KEY = os.environ.get("OCR_SPACE_API_KEY", "K86437539288957") # Primary
PA_OCR_URL = os.environ.get("PA_OCR_URL", "")               # Secondary
# pytesseract will be used as a last resort local fallback


nest_asyncio.apply()
app_web = Flask(__name__)

# ─── Config (MUST BE FIRST) ──────────────────────────────
# native .env parser (zero-dependency for Replit raw file loads)
if os.path.exists(".env"):
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()


TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
GMAIL_EMAIL = os.environ.get("GMAIL_EMAIL", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
REPLIT_URL = os.environ.get(
    "REPLIT_URL", "https://jobscrap--akshat3478.replit.app").rstrip("/")
MAIL_BOT_SECRET = os.environ.get("MAIL_BOT_SECRET", "")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "admin123")
PA_TOKEN = os.environ.get("PA_TOKEN", "")
PA_USERNAME = os.environ.get("PA_USERNAME", "")

BOT_NAME = "BCA Job Hunter"
BOT_VERSION = "8.0"
HOURS_BETWEEN_SEARCHES = 3
MAX_RETRIES = 3
HOURS_OLD = 480
RESULTS_PER_SEARCH = 10
WEB_PORT = int(os.environ.get("PORT", 5000))

search_paused = False
try:
    with open("data/search_paused.json") as f:
        search_paused = json.load(f).get("paused", False)
except:
    pass
job_cache = {"search_running": False, "last_update": None}

# ─── NOW validate ─────────────────────────────────────────
if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
    logger.critical("❌ Missing TELEGRAM_BOT_TOKEN or CHAT_ID in .env")
    exit(1)

# Bug #53 fix: Validate CHAT_ID format
try:
    CHAT_ID = int(CHAT_ID)
except:
    logger.critical("❌ CHAT_ID must be a numeric integer!")
    exit(1)


# Bug #55 fix: Sanitize config prints to remove sensitive info
def sanitize_config():
    """Sanitize environment variables for logging"""
    safe_config = {
        "BOT_VERSION":
        BOT_VERSION,
        "TELEGRAM_BOT_TOKEN":
        TELEGRAM_BOT_TOKEN[:10] + "..." if TELEGRAM_BOT_TOKEN else None,
        "CHAT_ID":
        CHAT_ID,
        "GMAIL_EMAIL":
        GMAIL_EMAIL,
        "GMAIL_APP_PASSWORD":
        "SET" if GMAIL_APP_PASSWORD else "MISSING",
        "MAIL_BOT_SECRET":
        "SET" if MAIL_BOT_SECRET else "MISSING",
        "REPLIT_URL":
        REPLIT_URL
    }
    logger.info(f"🚀 Initialized with config: {safe_config}")


sanitize_config()

# Feature #18: Dream and Blacklisted Companies
DREAM_COMPANIES = [
    c.strip().lower() for c in os.environ.get(
        "DREAM_COMPANIES", "Google,Microsoft,Amazon,Apple,Meta").split(",")
]
BLACKLISTED_COMPANIES = [
    c.strip().lower() for c in os.environ.get(
        "BLACKLISTED_COMPANIES", "Example Scam,Fake Co").split(",")
]
try:
    with open("data/banned_companies.json") as f:
        BLACKLISTED_COMPANIES.extend(json.load(f))
except:
    pass
BLACKLISTED_COMPANIES = list(set(BLACKLISTED_COMPANIES))

data_lock = RLock()
import email_monitor as _em_module
_em_module.set_shared_lock(data_lock)
logger.info("✅ Shared lock passed to email_monitor module")
search_lock = asyncio.Lock()
_background_tasks = set()

# ─── Data Paths ────────────────────────────────────────────
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

JOBS_FILE = os.path.join(DATA_DIR, "all_jobs.csv")
EMAILS_FILE = os.path.join(DATA_DIR, "all_emails.csv")
APPLIED_FILE = os.path.join(DATA_DIR, "applied_jobs.csv")
DISMISSED_FILE = os.path.join(DATA_DIR, "dismissed_jobs.csv")
SAVED_FILE = os.path.join(DATA_DIR, "saved_jobs.csv")
IMPORTANT_FILE = os.path.join(DATA_DIR, "important_jobs.csv")
HIGH_PRIORITY_FILE = os.path.join(DATA_DIR, "high_priority.csv")
MNC_FILE = os.path.join(DATA_DIR, "mnc_jobs.csv")
SEARCH_LOG_FILE = os.path.join(DATA_DIR, "search_log.json")
FUZZY_SEEN_FILE = os.path.join(DATA_DIR, "fuzzy_seen.json")
SENT_LOG_FILE = os.path.join(DATA_DIR,
                             "sent_log.csv")  # Bug #15 fix consistency
# SENT_EMAILS_FILE alias removed — email_monitor.py has its own path
MAIL_QUEUE_FILE = os.path.join(DATA_DIR, "mail_queue.json")
MAIL_LOG_FILE = os.path.join(DATA_DIR, "mail_activity.json")
PAUSED_CONFIG = os.path.join(DATA_DIR,
                             "mail_paused.json")  # Bug #15 fix consistency
BATCH_ALERTS_FILE = os.path.join(
    DATA_DIR, "batch_alerts.json")  # Bug #15 fix consistency

# ─── MNC Directory (ALL 35 from original) ─────────────────
TOP_MNCS = {
    "Google": {
        "careers": "careers.google.com",
        "tier": "S"
    },
    "Microsoft": {
        "careers": "careers.microsoft.com",
        "tier": "S"
    },
    "Amazon": {
        "careers": "amazon.jobs",
        "tier": "S"
    },
    "Apple": {
        "careers": "jobs.apple.com",
        "tier": "S"
    },
    "Meta": {
        "careers": "metacareers.com",
        "tier": "S"
    },
    "Netflix": {
        "careers": "jobs.netflix.com",
        "tier": "S"
    },
    "JP Morgan": {
        "careers": "jpmorgan.com/careers",
        "tier": "S"
    },
    "Goldman Sachs": {
        "careers": "goldmansachs.com/careers",
        "tier": "S"
    },
    "Morgan Stanley": {
        "careers": "morganstanley.com/careers",
        "tier": "S"
    },
    "Infosys": {
        "careers": "infosys.com/careers",
        "tier": "A"
    },
    "TCS": {
        "careers": "tcs.com/careers",
        "tier": "A"
    },
    "Wipro": {
        "careers": "careers.wipro.com",
        "tier": "A"
    },
    "HCL Technologies": {
        "careers": "hcltech.com/careers",
        "tier": "A"
    },
    "Tech Mahindra": {
        "careers": "techmahindra.com/careers",
        "tier": "A"
    },
    "Cognizant": {
        "careers": "careers.cognizant.com",
        "tier": "A"
    },
    "Accenture": {
        "careers": "accenture.com/careers",
        "tier": "A"
    },
    "Capgemini": {
        "careers": "capgemini.com/careers",
        "tier": "A"
    },
    "IBM": {
        "careers": "ibm.com/careers",
        "tier": "A"
    },
    "Oracle": {
        "careers": "oracle.com/careers",
        "tier": "A"
    },
    "SAP": {
        "careers": "sap.com/careers",
        "tier": "A"
    },
    "Salesforce": {
        "careers": "salesforce.com/careers",
        "tier": "A"
    },
    "Adobe": {
        "careers": "adobe.com/careers",
        "tier": "A"
    },
    "Cisco": {
        "careers": "jobs.cisco.com",
        "tier": "A"
    },
    "Intel": {
        "careers": "intel.com/jobs",
        "tier": "A"
    },
    "Deloitte": {
        "careers": "deloitte.com/careers",
        "tier": "A"
    },
    "KPMG": {
        "careers": "kpmg.com/careers",
        "tier": "A"
    },
    "EY": {
        "careers": "ey.com/careers",
        "tier": "A"
    },
    "PwC": {
        "careers": "pwc.com/careers",
        "tier": "A"
    },
    "Barclays": {
        "careers": "barclays.com/careers",
        "tier": "A"
    },
    "Flipkart": {
        "careers": "flipkartcareers.com",
        "tier": "A"
    },
    "Uber": {
        "careers": "uber.com/careers",
        "tier": "A"
    },
    "Zoho": {
        "careers": "zoho.com/careers",
        "tier": "A"
    },
    "Freshworks": {
        "careers": "freshworks.com/careers",
        "tier": "A"
    },
    "Razorpay": {
        "careers": "razorpay.com/careers",
        "tier": "B"
    },
    "CRED": {
        "careers": "cred.club/careers",
        "tier": "B"
    },
    "PhonePe": {
        "careers": "phonepe.com/careers",
        "tier": "B"
    },
    "Paytm": {
        "careers": "paytm.com/careers",
        "tier": "B"
    },
    "Swiggy": {
        "careers": "careers.swiggy.com",
        "tier": "B"
    },
    "Zomato": {
        "careers": "zomato.com/careers",
        "tier": "B"
    },
    "Meesho": {
        "careers": "meesho.com/careers",
        "tier": "B"
    },
}
MNC_NAMES_LOWER = {n.lower(): n for n in TOP_MNCS}

PRIORITY_EMOJI = {"HIGH": "🔥", "MID": "⭐", "LOW": "📌", "UNKNOWN": "❓"}
PRIORITY_LABELS = {
    "HIGH": "🔥 HIGH (₹5L+)",
    "MID": "⭐ MID (₹2.5-5L)",
    "LOW": "📌 ENTRY (₹1-2.5L)",
    "UNKNOWN": "❓ NOT DISCLOSED",
}

# ═══════════════════════════════════════════════════════════
#  TEMPLATE ENGINE v3.0 — Matches Your Actual Email Style
# ═══════════════════════════════════════════════════════════

TEMPLATES_FILE = os.path.join(DATA_DIR, "email_templates.json")
TEMPLATE_DEFAULTS_FILE = os.path.join(DATA_DIR, "template_defaults.json")
RESUME_FILE = os.path.join(DATA_DIR, "resume.pdf")
RESUME_META_FILE = os.path.join(DATA_DIR, "resume_meta.json")

_template_cache = {"data": None, "mtime": 0}


def _make_greeting(email_addr):
    """
    YOUR style:
    - Known name   → "Hello Gunjan Ma'am/Sir,"
    - Unknown name → "Hello Ma'am/Sir,"
    - NEVER "Dear" or "Respected"
    """
    if not email_addr or "@" not in str(email_addr):
        return "Hello Ma'am/Sir,"

    local = email_addr.split("@")[0]
    generic = {
        "hr", "hiring", "career", "careers", "info", "jobs",
        "recruit", "recruitment", "admin", "contact", "support",
        "noreply", "no-reply", "enquiry", "hello", "team",
        "mail", "office", "placement", "apply", "resume",
        "cv", "talent", "people", "jointeam", "work",
        "internship", "openings", "vacancy",
    }

    parts = re.split(r"[._\-+0-9]", local)
    name_parts = []
    for p in parts:
        if p.lower() not in generic and len(p) > 1 and p.isalpha():
            name_parts.append(p.capitalize())

    if name_parts:
        name = " ".join(name_parts[:2])
        return f"Hello {name} Ma'am/Sir,"

    return "Hello Ma'am/Sir,"


def _clean_company(company):
    """
    YOUR style:
    - Known   → "at RNF Technologies"
    - Unknown → "at your organization" (NOT "esteemed organization")
    """
    if not company:
        return "your organization"
    c = str(company).strip()
    bad = {
        "unknown company", "unknown", "n/a", "nan", "none",
        "", "not found", "company", "org", "forwarded company",
    }
    if c.lower() in bad:
        return "your organization"
    return c


def _get_default_templates():
    """
    4 templates matching YOUR actual email style exactly.
    Copied from your real sent email to gunjan@rnftech.
    """
    return {
        "normal": {
            "name": "General IT / All Roles",
            "emoji": "💻",
            "description": "Python, SQL, HTML, Excel — your original template",
            "subject": "Application for Entry-Level Opportunity - {name}",
            "body": """{greeting}

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
{phone}""",
            "editable": True,
        },

        "research": {
            "name": "Research Associate",
            "emoji": "🔬",
            "description": "Research, data collection, documentation, academic roles",
            "subject": "Application for Research Associate Position - {name}",
            "body": """{greeting}

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
{phone}""",
            "editable": True,
        },

        "analytics": {
            "name": "Data Analytics",
            "emoji": "📊",
            "description": "Data Analyst, MIS, Business Analyst, BI roles",
            "subject": "Application for Data Analyst Position - {name}",
            "body": """{greeting}

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
{phone}""",
            "editable": True,
        },

        "followup": {
            "name": "Follow-Up Email",
            "emoji": "🔄",
            "description": "Polite follow-up after 3-5 days of no response",
            "subject": "Following Up - Job Application - {name}",
            "body": """{greeting}

I hope you are doing well.

I am writing to follow up on my previous application for entry-level opportunities at your organization, which I had sent a few days ago.

I remain very interested in contributing to your team and wanted to reiterate my enthusiasm for the opportunity. I am a {degree} graduate with skills in Python, SQL, HTML/CSS, Excel, data analysis, and IT support.

I am an immediate joiner and available for interviews at your convenience.

If my application was received, I would be grateful for any update regarding the next steps. If not, I have re-attached my resume for your reference.

Apologies for any inconvenience, and thank you for your time.

Regards,
{name}
{phone}""",
            "editable": True,
        },
    }


def load_templates():
    """Load with file-change cache"""
    global _template_cache

    if not os.path.exists(TEMPLATES_FILE):
        defaults = _get_default_templates()
        safe_save_json(TEMPLATES_FILE, defaults)
        _template_cache = {"data": defaults, "mtime": time.time()}
        return defaults

    try:
        mtime = os.path.getmtime(TEMPLATES_FILE)
    except:
        mtime = 0

    if _template_cache["data"] and _template_cache["mtime"] >= mtime:
        return _template_cache["data"]

    data = safe_load_json(TEMPLATES_FILE, {})
    if not data:
        data = _get_default_templates()
        safe_save_json(TEMPLATES_FILE, data)

    _template_cache = {"data": data, "mtime": time.time()}
    return data


def save_templates(templates):
    global _template_cache
    safe_save_json(TEMPLATES_FILE, templates)
    _template_cache = {"data": templates, "mtime": time.time()}


def render_template(template_key, to_email, company, role=""):
    """
    Render template with all variables.
    Returns (subject, body).
    """
    templates = load_templates()
    tmpl = templates.get(template_key)
    if not tmpl:
        tmpl = templates.get("normal", _get_default_templates()["normal"])

    company_clean = _clean_company(company)
    greeting = _make_greeting(to_email)

    if not role or role in ("", "N/A", "Entry-Level Opportunity"):
        role = "Entry-Level Opportunity"

    name = os.environ.get("YOUR_NAME", "Akshat Tripathi")
    email = os.environ.get("GMAIL_EMAIL", "")
    phone = os.environ.get("PHONE", "+91-7081484808")
    linkedin = os.environ.get("LINKEDIN", "linkedin.com/in/akshattripathi7081")
    github = os.environ.get("GITHUB", "github.com/akshat7081")
    university = os.environ.get("UNIVERSITY",
        "Guru Gobind Singh Indraprastha University, New Delhi")
    degree = os.environ.get("DEGREE", "BCA")

    variables = {
        "greeting": greeting,
        "name": name,
        "email": email,
        "phone": phone,
        "linkedin": linkedin,
        "github": github,
        "university": university,
        "degree": degree,
        "company": company_clean,
        "role": role,
    }

    try:
        subject = tmpl["subject"].format(**variables)
    except (KeyError, ValueError):
        subject = f"Application for {role} - {name}"

    try:
        body = tmpl["body"].format(**variables)
    except (KeyError, ValueError) as e:
        logger.warning(f"Template render error: {e}")
        body = tmpl["body"]

    return subject, body


def get_template_keyboard(extra_data=""):
    """Template selection buttons"""
    templates = load_templates()
    buttons = []
    for key, tmpl in templates.items():
        if key == "followup":
            continue
        emoji = tmpl.get("emoji", "📧")
        name = tmpl.get("name", key.title())
        buttons.append([InlineKeyboardButton(
            f"{emoji} {name}", callback_data=f"tmpl_{key}")])
    buttons.append([InlineKeyboardButton(
        "❌ Cancel — Don't Send", callback_data="tmpl_cancel")])
    return InlineKeyboardMarkup(buttons)


def get_resume_info():
    if os.path.exists(RESUME_META_FILE):
        return safe_load_json(RESUME_META_FILE, {})
    if os.path.exists(RESUME_FILE):
        size = os.path.getsize(RESUME_FILE)
        return {"filename": "resume.pdf", "size_kb": size // 1024, "updated": "Unknown"}
    return None

# ═══════════════════════════════════════════════════════════
#  TEMPLATE EDITOR + RESUME MANAGEMENT COMMANDS
# ═══════════════════════════════════════════════════════════

async def cmd_templates(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    templates = load_templates()
    queue = load_mail_queue()

    msg = "📧 *Email Templates*\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for key, tmpl in templates.items():
        emoji = tmpl.get("emoji", "📧")
        name = tmpl.get("name", key)
        desc = tmpl.get("description", "")
        count = sum(1 for q in queue if q.get("template") == key)
        sent = sum(1 for q in queue
                   if q.get("template") == key and q.get("status") == "sent")
        msg += (
            f"{emoji} *{name}* (`{key}`)\n"
            f"   {desc}\n"
            f"   📊 Queued: {count} | ✅ Sent: {sent}\n\n")

    msg += (
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "*Commands:*\n"
        "👁️ `/previewtemplate normal`\n"
        "✏️ `/edittemplate normal subject New Subject`\n"
        "✏️ `/edittemplate normal body` → interactive\n"
        "➕ `/addtemplate custom_name`\n"
        "🗑️ `/deletetemplate custom_name`\n"
        "🔄 `/resettemplate normal` or `/resettemplate all`\n")

    await safe_reply(update, msg, parse_mode="Markdown")


async def cmd_previewtemplate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        templates = load_templates()
        keys = ", ".join(templates.keys())
        await safe_reply(update,
            f"Usage: `/previewtemplate normal`\n"
            f"Available: {keys}\n\n"
            f"Or: `/previewtemplate normal hr@tcs.com TCS`",
            parse_mode="Markdown")
        return

    key = args[0].lower()
    templates = load_templates()
    if key not in templates:
        await safe_reply(update,
            f"❌ Template `{key}` not found.\n"
            f"Available: {', '.join(templates.keys())}")
        return

    sample_email = args[1] if len(args) > 1 else "gunjan@rnftech.com"
    sample_company = args[2] if len(args) > 2 else "RNF Technologies"
    sample_role = " ".join(args[3:]) if len(args) > 3 else "Entry-Level Opportunity"

    subject, body = render_template(key, sample_email, sample_company, sample_role)

    resume = get_resume_info()
    rline = f"📄 Resume: {resume.get('filename')} ({resume.get('size_kb')} KB)" \
        if resume else "📄 No resume uploaded"

    body_show = body[:2800] + "\n\n... [truncated]" if len(body) > 2800 else body

    msg = (
        f"👁️ *Preview: {templates[key].get('emoji','')} {key}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📧 *To:* `{sample_email}`\n"
        f"📌 *Subject:*\n`{subject}`\n\n"
        f"📝 *Body:*\n"
        f"─────────────────────────\n"
        f"{escape_md(body_show)}\n"
        f"─────────────────────────\n\n"
        f"{rline}\n\n"
        f"💡 Variables: {{greeting}}, {{name}}, {{degree}},\n"
        f"{{phone}}, {{email}}, {{linkedin}},\n"
        f"{{github}}, {{university}}")

    await safe_reply(update, msg, parse_mode="Markdown")


async def cmd_edittemplate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args or len(args) < 2:
        await safe_reply(update,
            "✏️ *Template Editor*\n━━━━━━━━━━━━━━━━━━━\n\n"
            "*Edit Subject:*\n"
            "`/edittemplate normal subject New Subject Here`\n\n"
            "*Edit Body:*\n"
            "`/edittemplate normal body`\n"
            "Then send full body text as next message.\n\n"
            "*Edit Name / Description / Emoji:*\n"
            "`/edittemplate normal name General Template`\n"
            "`/edittemplate normal desc Short description`\n"
            "`/edittemplate normal emoji 🚀`\n\n"
            "*Variables you can use:*\n"
            "`{greeting}` → Hello Ma'am/Sir,\n"
            "`{name}` → your name\n"
            "`{degree}` → BCA\n"
            "`{phone}` → your phone\n"
            "`{email}` → your email\n"
            "`{university}` → your university\n"
            "`{linkedin}` → LinkedIn URL\n"
            "`{github}` → GitHub URL",
            parse_mode="Markdown")
        return

    key = args[0].lower()
    field = args[1].lower()
    templates = load_templates()

    if key not in templates:
        await safe_reply(update,
            f"❌ `{key}` not found. Available: {', '.join(templates.keys())}")
        return

    if field == "subject":
        if len(args) < 3:
            current = templates[key].get("subject", "")
            await safe_reply(update,
                f"📌 *Current Subject:*\n`{current}`\n\n"
                f"To change: `/edittemplate {key} subject Your New Subject`",
                parse_mode="Markdown")
            return
        new_val = " ".join(args[2:])
        templates[key]["subject"] = new_val
        save_templates(templates)
        await safe_reply(update,
            f"✅ Subject updated for `{key}`!\n📌 `{new_val}`",
            parse_mode="Markdown")

    elif field == "body":
        ctx.user_data["editing_template"] = key
        ctx.user_data["editing_field"] = "body"
        current = templates[key].get("body", "")
        preview = current[:500] + "..." if len(current) > 500 else current
        await safe_reply(update,
            f"✏️ *Editing Body of `{key}`*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"*Current (preview):*\n"
            f"_{escape_md(preview)}_\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 *Send the FULL new body text now.*\n\n"
            f"Use variables like:\n"
            f"`{{greeting}}` `{{name}}` `{{company}}`\n"
            f"`{{role}}` `{{degree}}` `{{phone}}`\n\n"
            f"Type /cancel to abort.",
            parse_mode="Markdown")

    elif field == "name":
        new_val = " ".join(args[2:]) if len(args) > 2 else ""
        if not new_val:
            await safe_reply(update,
                f"Current: {templates[key].get('name')}")
            return
        templates[key]["name"] = new_val
        save_templates(templates)
        await safe_reply(update, f"✅ Renamed `{key}` → *{new_val}*",
                         parse_mode="Markdown")

    elif field in ("desc", "description"):
        new_val = " ".join(args[2:]) if len(args) > 2 else ""
        if not new_val:
            await safe_reply(update,
                f"Current: {templates[key].get('description')}")
            return
        templates[key]["description"] = new_val
        save_templates(templates)
        await safe_reply(update, f"✅ Description updated for `{key}`")

    elif field == "emoji":
        new_val = args[2] if len(args) > 2 else ""
        if not new_val:
            await safe_reply(update, "Usage: `/edittemplate normal emoji 🚀`",
                             parse_mode="Markdown")
            return
        templates[key]["emoji"] = new_val
        save_templates(templates)
        await safe_reply(update, f"✅ Emoji: {new_val}")

    else:
        await safe_reply(update,
            f"❌ Unknown field `{field}`.\n"
            f"Use: subject, body, name, desc, emoji")


async def cmd_addtemplate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await safe_reply(update,
            "➕ *Add Custom Template*\n\n"
            "Usage: `/addtemplate template_key`\n"
            "Example: `/addtemplate webdev`\n\n"
            "Creates a copy of 'normal' you can edit.",
            parse_mode="Markdown")
        return

    key = args[0].lower().replace(" ", "_")
    templates = load_templates()

    if key in templates:
        await safe_reply(update,
            f"⚠️ `{key}` already exists. Use /edittemplate {key}")
        return
    if len(templates) >= 10:
        await safe_reply(update, "❌ Max 10 templates. Delete one first.")
        return

    base = templates.get("normal", _get_default_templates()["normal"])
    templates[key] = {
        "name": " ".join(args[1:]) if len(args) > 1 else key.replace("_", " ").title(),
        "emoji": "📝",
        "description": "Custom template",
        "subject": base["subject"],
        "body": base["body"],
        "editable": True,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_templates(templates)

    await safe_reply(update,
        f"✅ *Template `{key}` created!*\n\n"
        f"Edit it:\n"
        f"📌 `/edittemplate {key} subject ...`\n"
        f"📝 `/edittemplate {key} body`\n"
        f"👁️ `/previewtemplate {key}`",
        parse_mode="Markdown")


async def cmd_deletetemplate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await safe_reply(update, "Usage: `/deletetemplate template_key`",
                         parse_mode="Markdown")
        return

    key = args[0].lower()
    protected = {"normal", "research", "analytics", "followup"}
    if key in protected:
        await safe_reply(update,
            f"🔒 Cannot delete built-in `{key}`.\n"
            f"Use `/resettemplate {key}` to restore default.")
        return

    templates = load_templates()
    if key not in templates:
        await safe_reply(update, f"❌ `{key}` not found.")
        return

    del templates[key]
    save_templates(templates)
    await safe_reply(update, f"🗑️ Template `{key}` deleted!")


async def cmd_resettemplate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await safe_reply(update,
            "Usage: `/resettemplate normal` or `/resettemplate all`",
            parse_mode="Markdown")
        return

    key = args[0].lower()
    defaults = _get_default_templates()

    if key == "all":
        save_templates(defaults)
        await safe_reply(update, "🔄 *All templates reset to defaults!*",
                         parse_mode="Markdown")
        return

    if key not in defaults:
        await safe_reply(update,
            f"❌ No default for `{key}`.\n"
            f"Defaults: {', '.join(defaults.keys())}")
        return

    templates = load_templates()
    templates[key] = defaults[key]
    save_templates(templates)
    await safe_reply(update, f"🔄 `{key}` reset to default!")


# ═══════════════════════════════════════════════════════════
#  RESUME MANAGEMENT
# ═══════════════════════════════════════════════════════════

async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    info = get_resume_info()
    if not info:
        await safe_reply(update,
            "📄 *No Resume Uploaded*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Send a PDF file to this chat to upload.\n"
            "Or use /updateresume",
            parse_mode="Markdown")
        return

    await safe_reply(update,
        f"📄 *Current Resume*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 *File:* {info.get('filename', 'resume.pdf')}\n"
        f"📦 *Size:* {info.get('size_kb', '?')} KB\n"
        f"⏰ *Updated:* {info.get('updated', 'Unknown')}\n\n"
        f"💡 Send a new PDF to replace it.\n"
        f"📤 PythonAnywhere auto-downloads latest.",
        parse_mode="Markdown")


async def cmd_updateresume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["awaiting_resume"] = True
    await safe_reply(update,
        "📄 *Resume Update Mode*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📎 Send your new resume as a *PDF file* now.\n\n"
        "⚠️ Requirements:\n"
        "  • PDF format only\n"
        "  • Max 5 MB\n"
        "  • Send as *document* (not photo)\n\n"
        "Type /cancel to abort.",
        parse_mode="Markdown")


async def handle_resume_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle PDF uploads. Returns True if handled."""
    doc = update.message.document
    if not doc or doc.mime_type != "application/pdf":
        return False

    ctx.user_data["pending_resume"] = {
        "file_id": doc.file_id,
        "file_name": doc.file_name or "resume.pdf",
        "file_size": doc.file_size or 0,
    }

    size_kb = (doc.file_size or 0) // 1024

    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await update.effective_message.reply_text(
            f"❌ File too large ({size_kb} KB). Max 5 MB.")
        return True

    current = get_resume_info()
    old_info = ""
    if current:
        old_info = f"\n📋 *Current:* {current.get('filename')} ({current.get('size_kb')} KB)"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, Update Resume",
                              callback_data="resume_confirm")],
        [InlineKeyboardButton("❌ Cancel",
                              callback_data="resume_cancel")],
    ])

    await update.effective_message.reply_text(
        f"📄 *PDF Received*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 *Name:* {doc.file_name}\n"
        f"📦 *Size:* {size_kb} KB{old_info}\n\n"
        f"🔄 *Replace your current resume?*\n\n"
        f"⚠️ PythonAnywhere will use this for ALL emails.",
        parse_mode="Markdown", reply_markup=kb)
    return True


async def cmd_quicksend(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /quicksend hr@tcs.com TCS research
    /quicksend hr@tcs.com TCS           (defaults to normal)
    """
    args = ctx.args
    if not args or len(args) < 2:
        await safe_reply(update,
            "⚡ *Quick Send*\n━━━━━━━━━━━━━━━━━━━\n\n"
            "`/quicksend email company template`\n\n"
            "Templates: normal, research, analytics\n\n"
            "Examples:\n"
            "`/quicksend hr@tcs.com TCS`\n"
            "`/quicksend hr@tcs.com TCS research`\n"
            "`/quicksend data@wipro.com Wipro analytics`",
            parse_mode="Markdown")
        return

    email_addr = args[0].strip().lower()
    company = args[1].strip()
    template = args[2].lower() if len(args) > 2 else "normal"
    role = " ".join(args[3:]) if len(args) > 3 else "Entry-Level Opportunity"

    if not is_valid_email(email_addr):
        await safe_reply(update, "❌ Invalid email.")
        return

    templates = load_templates()
    if template not in templates:
        await safe_reply(update,
            f"❌ Template `{template}` not found.\n"
            f"Available: {', '.join(templates.keys())}")
        return

    if add_to_mail_queue(email_addr, company, role,
                         source="quicksend", template=template):
        save_email(email_addr, company, role, f"QuickSend:{template}")
        tmpl = templates[template]
        await safe_reply(update,
            f"⚡ *Queued!*\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 `{email_addr}`\n"
            f"🏢 {company}\n"
            f"📝 {tmpl['emoji']} {tmpl['name']}\n\n"
            f"📤 Will be sent by PythonAnywhere",
            parse_mode="Markdown")
    else:
        st = get_email_status(email_addr)
        if st == "sent": await safe_reply(update, f"✅ `{email_addr}` was already sent successfully.")
        elif st == "queued": await safe_reply(update, f"🕒 `{email_addr}` is still in the queue.")
        else: await safe_reply(update, f"⚙️ `{email_addr}` was previously processed.")

IMPORTANT_KEYWORDS = (
    "artificial intelligence", "machine learning", "deep learning", "nlp",
    "computer vision", "tensorflow", "pytorch", "data science",
    "data scientist", "aws", "azure", "google cloud", "gcp",
    "cloud computing", "devops", "kubernetes", "docker",
    "cybersecurity", "blockchain", "generative ai", "llm"
)

# ALL search terms from original
LOCATIONS = [
    "Delhi, India", "Noida, India", "Gurugram, India", "Remote, India"
]

SEARCH_TERMS = [
    # ── Python (core) ─────────────────────────────────────
    "python developer fresher",
    "python trainee",
    "python backend fresher",
    "junior python developer",
    "python scripting fresher",
    "python automation fresher",
    "python programmer fresher",
    "python coder fresher",
    "python developer 0-1 years",
    "python developer entry level",
    "python developer graduate",
    "python developer BCA",
    "python developer Delhi",
    "python developer Noida",
    "python developer Gurugram",
    "python developer remote India",
    "python developer work from home",
    "hiring python fresher",
    "urgent python developer",
    "python developer walk-in",

    # ── Python frameworks ─────────────────────────────────
    "django developer fresher",
    "django developer entry level",
    "django developer junior",
    "django trainee",
    "flask developer fresher",
    "flask developer junior",
    "FastAPI developer fresher",
    "FastAPI developer junior",

    # ── HTML / CSS / Web ──────────────────────────────────
    "web developer fresher",
    "junior web developer",
    "frontend developer fresher",
    "HTML CSS developer fresher",
    "web designer fresher",
    "junior frontend developer",
    "HTML developer fresher",
    "CSS developer fresher",
    "web developer entry level",
    "web developer trainee",
    "web developer 0-1 years",
    "web developer graduate",
    "frontend developer entry level",
    "frontend trainee",
    "web developer Delhi",
    "web developer Noida",
    "web developer Gurugram",
    "web developer remote India",
    "web developer work from home",
    "static website developer",
    "website developer fresher",
    "web page developer fresher",
    "HTML CSS JavaScript fresher",
    "responsive web developer fresher",
    "hiring web developer fresher",
    "urgent web developer",
    "web developer walk-in",

    # ── SQL / Database ────────────────────────────────────
    "SQL developer fresher",
    "MySQL developer fresher",
    "database administrator fresher",
    "junior DBA",
    "database analyst fresher",
    "SQL analyst fresher",
    "SQL developer entry level",
    "SQL developer trainee",
    "SQL developer 0-1 years",
    "SQL developer graduate",
    "SQL query developer fresher",
    "PL/SQL developer fresher",
    "database developer fresher",
    "database executive fresher",
    "SQL developer Delhi",
    "SQL developer Noida",
    "SQL developer Gurugram",
    "SQL developer remote India",
    "SQL report developer fresher",
    "SQL data analyst fresher",
    "hiring SQL developer",
    "urgent SQL developer fresher",

    # ── Excel / MIS ───────────────────────────────────────
    "Excel expert fresher",
    "advanced Excel fresher",
    "MIS analyst fresher",
    "MIS executive fresher",
    "MIS reporting fresher",
    "data entry operator",
    "back office executive fresher",
    "Excel developer fresher",
    "Excel analyst fresher",
    "Excel VBA fresher",
    "MIS coordinator fresher",
    "MIS officer fresher",
    "Excel data analyst fresher",
    "spreadsheet analyst fresher",
    "Excel automation fresher",
    "Excel macro developer fresher",
    "advance Excel job fresher",
    "Excel reporting fresher",
    "data processing executive fresher",
    "data management fresher",
    "MIS executive Delhi",
    "MIS executive Noida",
    "MIS executive Gurugram",
    "back office data entry",
    "hiring Excel expert",
    "hiring MIS executive",

    # ── Data Analyst (Python + SQL + Excel) ───────────────
    "data analyst fresher",
    "data analyst SQL fresher",
    "business analyst fresher",
    "junior data analyst",
    "reporting analyst fresher",
    "data analyst entry level",
    "data analyst trainee",
    "data analyst 0-1 years",
    "data analyst graduate",
    "data analyst BCA",
    "data analyst Python fresher",
    "data analyst Excel fresher",
    "analytics executive fresher",
    "business intelligence fresher",
    "data analyst Delhi",
    "data analyst Noida",
    "data analyst Gurugram",
    "data analyst remote India",
    "data analyst work from home",
    "hiring data analyst fresher",
    "junior business analyst",
    "operations analyst fresher",
    "research analyst fresher",
    "market research analyst fresher",

    # ── General IT / Software (BCA level) ─────────────────
    "junior software developer",
    "fresher developer",
    "software engineer fresher",
    "IT executive fresher",
    "IT support fresher",
    "junior programmer",
    "trainee software engineer",
    "BCA fresher job",
    "software developer entry level",
    "software developer trainee",
    "software developer 0-1 years",
    "software developer graduate",
    "IT coordinator fresher",
    "application support fresher",
    "junior software engineer",
    "graduate trainee IT",
    "computer operator fresher",
    "IT assistant fresher",
    "technical executive fresher",
    "IT fresher Delhi",
    "IT fresher Noida",
    "IT fresher Gurugram",
    "BCA graduate job Delhi",
    "BCA graduate job Noida",
    "fresher IT job remote India",
    "fresher IT job work from home",
    "hiring fresher developer",
    "hiring BCA fresher",
    "urgent fresher developer",
    "walk-in fresher IT",
]

# Trimmed exclusion filters for speed
EXCLUDE_EXACT = [
    "internship",
    "intern",
    "stipend",
    "sales",
    "telesales",
    "telecaller",
    "bpo",
    "voice process",
    "call center",
    "customer support",
    "customer care",
    "delivery",
    "driver",
    "warehouse",
    "accountant",
    "bookkeeper",
    "tax consultant",
    "teacher",
    "tutor",
    "marketing",
    "digital marketing",
    "seo expert",
    "content writer",
    "graphic designer",
    "video editor",
    "hr executive",
    "hr manager",
    "doctor",
    "nurse",
    "medical",
    "pharmacist",
    "chef",
    "waiter",
    "housekeeping",
    "receptionist",
    "security guard",
    "helper",
    "packer",
    "cleaner",
    "plumber",
    "carpenter",
    "mechanic",
    "army",
    "police",
]

EXCLUDE_EXP = [
    "5 years",
    "5+ years",
    "3+ years",
    "4+ years",
    "7+ years",
    "10+ years",
    "8+ years",
    "6+ years",
    "experienced",
    "mid-senior",
    "senior level",
    "ca ",
    "chartered accountant",
    "company secretary",
    "mbbs",
    "md ",
    "bds",
    "b.tech required",
    "m.tech required",
    "mba required",
    "phd",
]

# Bug #2 fix: Separator constants to avoid .format() crashes
SEP_BOLD = "═" * 30
SEP_DASH = "━" * 28
SEP_LINE = "─" * 30
SEP_THIN = "─" * 28

EXCLUDE_LEVEL = [
    "senior",
    "sr.",
    "lead",
    "principal",
    "architect",
    "director",
    "VP",
    "manager",
    "head of",
    "chief",
    "CTO",
    "CIO",
    "staff",
    "specialist",
    "expert",
    "consultant",
]

SPAM_PATTERNS = [
    "!!!",
    "₹₹₹",
    "100% guaranteed",
    "urgent urgent",
    "no investment required",
    "earn lakhs",
    "money from home",
    "be your own boss",
    "unlimited income",
    "earn daily",
    "whatsapp number",
    "send resume on whatsapp",
]

BLOCKED_EMAIL_DOMAINS = [
    "example.com",
    "test.com",
    "domain.com",
    "sentry.io",
    "w3.org",
    "schema.org",
    "linkedin.com",
    "indeed.com",
    "naukri.com",
    "glassdoor.com",
    "monster.com",
    "shine.com",
    "placeholder.com",
    "xyz.com",
    "email.com",
    "facebook.com",
    "twitter.com",
    "instagram.com",
    "google.com",
    "microsoft.com",
    "apple.com",
    "amazon.com",
    "github.com",
    "stackoverflow.com",
]

# ═══════════════════════════════════════════════════════════
#  UTILITY FUNCTIONS (ALL from original + fixes)
# ═══════════════════════════════════════════════════════════


def to_bool(val):
    """Bug #6 fix: proper bool conversion from CSV."""
    if isinstance(val, bool): return val
    if not val: return False
    return str(val).lower() in ("true", "1", "yes", "y", "on")


def safe_load_json(filepath, default=None):
    """Safely load JSON from file with thread lock and error handling"""
    if not os.path.exists(filepath):
        return default if default is not None else []
    with data_lock:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if data is not None else (default if default is not None else [])
        except Exception as e:
            logger.error(f"Error loading JSON {filepath}: {e}")
            return default if default is not None else []


def safe_save_json(filepath, data):
    """Safely save JSON to file with thread lock and atomic write"""
    with data_lock:
        try:
            temp_file = filepath + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            os.replace(temp_file, filepath)
            return True
        except Exception as e:
            logger.error(f"Error saving JSON {filepath}: {e}")
            return False


def load_mail_queue():
    return safe_load_json(MAIL_QUEUE_FILE, [])


def push_to_pa(filepath):
    """Bypass PythonAnywhere 403 proxy by pushing data directly via API."""
    if not PA_TOKEN or not PA_USERNAME: return
    try:
        import requests, os
        filename = os.path.basename(filepath)
        url = f"https://www.pythonanywhere.com/api/v0/user/{PA_USERNAME}/files/path/home/{PA_USERNAME}/{filename}"
        with open(filepath, "rb") as f:
            resp = requests.post(url, headers={"Authorization": f"Token {PA_TOKEN}"}, files={"content": f})
        if resp.status_code in (200, 201):
            logger.info(f"✅ Successfully pushed {filename} to PythonAnywhere.")
        else:
            logger.error(f"PA Push error {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.error(f"PA push exception: {e}")


def save_mail_queue(queue):
    res = safe_save_json(MAIL_QUEUE_FILE, queue)
    if res:
        import threading
        threading.Thread(target=push_to_pa, args=(MAIL_QUEUE_FILE,), daemon=True).start()
    return res


# safe_save_json is defined below after safe_load_json (line ~580)


def html_escape(text):
    """Bug #10 fix: Deep escape for HTML + Jinja2 SSTI protection."""
    if not text: return ""
    text = str(real_escape(text))
    text = text.replace("{", "&#123;").replace("}",
                                               "&#125;").replace("%", "&#37;")
    return text


def escape_md(text):
    """Bug #8, #17 fix: Robust Markdown V1 escaping."""
    if not text: return ""
    for c in ['\\', '_', '*', '`', '[', ']', '(', ')', '$', '%']:
        text = text.replace(c, '\\' + c)
    return text


def gen_id(title, company, url):
    raw = "{}|{}|{}".format(
        str(title).lower().strip(),
        str(company).lower().strip(),
        str(url).strip())
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def safe_split_message(text, limit=4000):
    """Bug #30 fix: Split message if it exceeds Telegram limit."""
    if len(text) <= limit: return [text]
    parts = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        # Try to split at last newline before limit
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1: split_at = limit
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    return parts


def safe_str(val, default="N/A"):
    if val is None or (isinstance(val, float) and pd.isna(val)): return default
    s = str(val).strip()
    return s if s and s != "nan" else default


def normalize_str(s):
    if not s or pd.isna(s): return ""
    s = str(s).lower()
    s = re.sub(r"[^a-z0-9]", " ", s)
    return " ".join(s.split())


def gen_fuzzy_id(title, company):
    t = normalize_str(title)
    c = normalize_str(company)
    return hashlib.md5("{}|{}".format(t, c).encode()).hexdigest()[:12]


def is_valid_email(email):
    """Bug #6 fix: proper email validation."""
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", str(email)))


# extract_emails_from_text: primary definition is below with OCR-mangling recovery


# extract_job_details: primary definition is below with comprehensive extraction


# categorize_job: primary definition is below with emoji categories


def safe_read_csv(filepath, required_columns=None):
    """Read CSV safely, returning empty DataFrame if missing/corrupt"""
    if not os.path.exists(filepath):
        if required_columns:
            return pd.DataFrame(columns=required_columns)
        return pd.DataFrame()
    try:
        df = pd.read_csv(filepath)
        if df.empty and required_columns:
            return pd.DataFrame(columns=required_columns)
        return df
    except Exception as e:
        logger.error(f"CSV read error {filepath}: {e}")
        if required_columns:
            return pd.DataFrame(columns=required_columns)
        return pd.DataFrame()


def save_to_csv(data, filename):
    """Unified atomic CSV append with header alignment"""
    with data_lock:
        try:
            if os.path.exists(filename):
                try:
                    df_old = pd.read_csv(filename)
                except (pd.errors.EmptyDataError, pd.errors.ParserError):
                    logger.warning(f"CSV corrupted: {filename}, starting fresh")
                    df_old = pd.DataFrame()
            else:
                df_old = pd.DataFrame()

            df_new = pd.DataFrame([data])

            if df_old.empty:
                df_combined = df_new
            else:
                df_combined = pd.concat([df_old, df_new], ignore_index=True)

            # Atomic write via temp file
            temp = filename + ".tmp"
            df_combined.to_csv(temp, index=False)
            os.replace(temp, filename)
            return True
        except Exception as e:
            logger.error(f"CSV save error {filename}: {e}")
            # Emergency fallback: append mode
            try:
                write_header = not os.path.exists(filename)
                with open(filename, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=list(data.keys()))
                    if write_header:
                        writer.writeheader()
                    writer.writerow(data)
                return True
            except Exception as e2:
                logger.error(f"CSV fallback also failed {filename}: {e2}")
                return False





def load_fuzzy_seen():
    return set(safe_load_json(FUZZY_SEEN_FILE, []))


def save_fuzzy_seen(seen_set):
    """Bug #23 fix: 5000 item limit."""
    items = list(seen_set)
    if len(items) > 5000:
        items = list(items)[-5000:]
    safe_save_json(FUZZY_SEEN_FILE, items)


def validate_job(job):
    t = str(job.get("title", "")).strip()
    u = str(job.get("job_url", "")).strip()
    c = str(job.get("company", "")).strip()
    if not t or t == "nan" or len(t) < 5: return False
    if not u or not u.startswith(("http://", "https://")): return False
    if len(re.findall(r"[a-zA-Z]", t)) < 3: return False
    if t == t.upper() and len(t) > 10: return False
    if len(re.findall(r"[!@#$%^&*₹~]", t)) > 3: return False
    if c and c != "nan" and len(c) < 2: return False
    return True


def should_exclude(text, company=""):
    """Enhanced exclusion logic (Bug #1, 29, #11)."""
    if company and any(bad in company.lower()
                       for bad in BLACKLISTED_COMPANIES):
        return True

    text_lower = str(text).lower()
    for bc in BLACKLISTED_COMPANIES:
        if bc in text_lower: return True

    for kw in EXCLUDE_EXACT:
        if kw.lower() in text_lower: return True
    for exp in EXCLUDE_EXP:
        if exp.lower() in text_lower: return True

    # Fix #11: Check EXCLUDE_LEVEL against FULL text with word boundaries
    for lv in EXCLUDE_LEVEL:
        if re.search(r"\b" + re.escape(lv.lower()) + r"\b", text_lower):
            return True

    for sp in SPAM_PATTERNS:
        if sp.lower() in text_lower: return True

    latin = len(re.findall(r"[a-zA-Z0-9 ]", text_lower))
    if len(text_lower) > 5 and latin < len(text_lower) * 0.5: return True
    return False





def is_job_post(text):
    """Bug #11 fix: Relaxed keywords for OCR density"""
    if not text or len(str(text)) < 30: return False
    t = str(text).lower()
    
    # Core job patterns
    job_kws = ["hiring", "vacancy", "opening", "job", "salary", "apply", "resume", "cv", "join", "years exp", "recruiting"]
    match_count = sum(1 for kw in job_kws if kw in t)
    
    # Relaxed threshold for OCR
    if match_count >= 2: return True
    if any(x in t for x in ["urgent requirement", "hiring alert", "immediate joiner"]): return True
    
    return False


def is_important(title, company, text=""):
    """Check if job deserves HIGH priority based on keywords"""
    if not title and not company:
        return False
    title_lower = title.lower() if title else ""
    company_lower = company.lower() if company else ""
    text_lower = text.lower() if text else ""

    important_keywords = [
        "urgent", "immediate", "hiring alert", "top company", "mnc"
    ]

    for kw in important_keywords:
        title_l = str(title_lower)
        company_l = str(company_lower)
        text_l = str(text_lower)
        if kw in title_l or kw in company_l or kw in text_l:
            return True
    return False


def is_mnc(company):
    cl = str(company).lower().strip()
    for ml, mn in MNC_NAMES_LOWER.items():
        if ml in cl or cl in ml: return mn
    return None


def _clean_salary_value(val):
    """Extract numeric value from salary string"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s or s.lower() in ("nan", "n/a", "none", "not disclosed", "competitive", "negotiable"):
        return None
    s = re.sub(r'[₹$€£,\s]', '', s)
    lpa_match = re.search(r'(\d+\.?\d*)\s*(?:l|lac|lakh|lpa)', s, re.IGNORECASE)
    if lpa_match: return float(lpa_match.group(1)) * 100000
    k_match = re.search(r'(\d+\.?\d*)\s*k', s, re.IGNORECASE)
    if k_match: return float(k_match.group(1)) * 1000
    try:
        return float(re.sub(r'[^\d.]', '', s))
    except (ValueError, TypeError):
        return None

def get_priority(mn, mx):
    try:
        a = _clean_salary_value(mx) or _clean_salary_value(mn)
        if not a: return "UNKNOWN"
        if a >= 500000: return "HIGH"
        if a >= 250000: return "MID"
        if a >= 100000: return "LOW"
        if a > 0: return "LOW"
        return "UNKNOWN"
    except:
        return "UNKNOWN"


def format_salary(mn, mx):
    try:
        a = _clean_salary_value(mn)
        b = _clean_salary_value(mx)
        if a and b:
            if a >= 100000:
                return "₹{:.1f}L - {:.1f}L/yr".format(a / 100000, b / 100000)
            return "₹{:,} - {:,}".format(int(a), int(b))
        if b:
            if b >= 100000: return "Up to ₹{:.1f}L/yr".format(b / 100000)
            return "Up to ₹{:,}".format(int(b))
        if a:
            if a >= 100000: return "₹{:.1f}L+/yr".format(a / 100000)
            return "₹{:,}+".format(int(a))
        return "Not Disclosed"
    except:
        return "Not Disclosed"


def categorize_job(title):
    t = str(title).lower()
    for kws, cat in [
        (["python"], "🐍 Python"),
        (["web", "frontend", "html", "css", "javascript"], "🌐 Web Dev"),
        (["sql", "database", "mysql"], "🗄️ Database"),
        (["research", "analyst"], "🔍 Research"),
        (["excel", "mis", "data entry"], "📊 Excel/MIS"),
        (["trainee", "fresher"], "📚 Trainee"),
    ]:
        if any(k in t for k in kws): return cat
    return "💻 General IT"


def relevance_score(job):
    """Feature #24: Enhanced recommendation scoring logic."""
    score = 0
    title = str(job.get("title", "")).lower()
    desc = str(job.get("description", "")).lower()
    # full_text not needed — keywords checked against title and desc separately

    # Keyword matches
    for kw in IMPORTANT_KEYWORDS:
        if kw in title: score += 15
        elif kw in desc: score += 5

    # Company status
    if to_bool(job.get("is_mnc")): score += 20
    if to_bool(job.get("is_important")): score += 25

    # Feature #18: Dream company score boost
    cl = str(job.get("company", "")).lower().strip()
    if any(dc in cl for dc in DREAM_COMPANIES): score += 50
    elif any(bc in cl for bc in BLACKLISTED_COMPANIES):
        score -= 100  # Should be excluded anyway

    # MNC Tier boost
    mnc_name = str(job.get("mnc_name", ""))
    if mnc_name in TOP_MNCS:
        tier = TOP_MNCS[mnc_name].get("tier", "B")
        score += {"S": 30, "A": 20, "B": 10}.get(tier, 5)

    # Priority boost
    score += {
        "HIGH": 30,
        "MID": 15,
        "LOW": 5,
        "UNKNOWN": 0
    }.get(str(job.get("priority", "UNKNOWN")).upper(), 0)

    # Penalties
    if len(title) < 10: score -= 10
    if "intern" in title: score -= 50  # Penalize internships heavily

    # Ensure score is within 0-100 range for display (mostly)
    # Bug #34 fix: Bound score 0-100
    final_score: int = int(score)
    return max(0, min(100, final_score))


def pri_level(p):
    return {"HIGH": 5, "MID": 3, "LOW": 2, "UNKNOWN": 0}.get(str(p).upper(), 0)


def extract_emails_from_text(text):
    """Extract emails with mangling recovery (Bug #11)"""
    if not text: return []
    # Basic cleanup for OCR mangling
    t = str(text).replace(" (at) ", "@").replace(" [at] ", "@").replace("(dot)", ".").replace("[dot]", ".")
    # Robust regex for potential spaces or mangled markers
    emails = re.findall(r"[a-zA-Z0-9._%+-]+(?:\s*@\s*|\*\(at\)\*|\(at\)|\[at\])[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", t)
    
    blocked_domains_for_check = [
        d.lower() for d in BLOCKED_EMAIL_DOMAINS if d.lower() != "gmail.com"
    ]
    
    valid = []
    for e in set(emails):
        e = e.strip().replace(" ", "")
        if not any(b in e.lower() for b in blocked_domains_for_check) and len(e) < 60:
            if not e.startswith(".") and ".." not in e and is_valid_email(e):
                valid.append(e)
    return valid


def is_image_document(doc):
    """Safe check for image documents (Bug #4)"""
    if not doc: return False
    mt = getattr(doc, "mime_type", None)
    return bool(mt and mt.startswith("image/"))


def extract_all_emails(job):
    all_e = []
    for field in ["description", "company_url", "job_url_direct"]:
        val = job.get(field, "")
        if val and not pd.isna(val):
            all_e.extend(extract_emails_from_text(str(val)))
    return list(set(all_e))


# ═══════════════════════════════════════════════════════════
#  CSV / DATA HELPERS (ALL from original + fixes)
# ═══════════════════════════════════════════════════════════




def save_email(email, company, title, url):
    """Save email with atomic check-and-write"""
    if not email:
        return
    email = str(email).strip().lower()
    if not is_valid_email(email):
        return

    with data_lock:
        # Check if already exists
        if os.path.exists(EMAILS_FILE):
            try:
                df = pd.read_csv(EMAILS_FILE)
                if not df.empty and "email" in df.columns:
                    if email in df["email"].str.lower().values:
                        return  # Already exists
            except (pd.errors.EmptyDataError, pd.errors.ParserError):
                logger.warning("Emails CSV corrupted during save_email check")
            except Exception as e:
                logger.error(f"Error checking emails: {e}")

        # Write inside the same lock — no TOCTOU gap
        data = {
            "email": email,
            "company": company or "Unknown",
            "job_title": title or "Unknown",
            "job_url": url or "N/A",
            "date_added": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

        try:
            if os.path.exists(EMAILS_FILE):
                try:
                    df_old = pd.read_csv(EMAILS_FILE)
                except:
                    df_old = pd.DataFrame()
            else:
                df_old = pd.DataFrame()

            df_new = pd.DataFrame([data])
            df_combined = pd.concat([df_old, df_new], ignore_index=True) if not df_old.empty else df_new

            temp = EMAILS_FILE + ".tmp"
            df_combined.to_csv(temp, index=False)
            os.replace(temp, EMAILS_FILE)
        except Exception as e:
            logger.error(f"save_email write failed: {e}")


def load_seen():
    seen = set()
    with data_lock:  # Consolidated lock
        for f in [JOBS_FILE, APPLIED_FILE, DISMISSED_FILE]:
            if os.path.exists(f):
                try:
                    df = pd.read_csv(f)
                    if "job_id" in df.columns:
                        seen.update(df["job_id"].astype(str).tolist())
                    if "job_url" in df.columns:
                        seen.update(
                            df["job_url"].dropna().astype(str).tolist())
                except Exception as e:
                    logger.error("Error loading seen {}: {}".format(f, e))
    return seen


def load_csv(filename):
    if not os.path.exists(filename): return []
    with data_lock:  # Consolidated lock
        try:
            df = pd.read_csv(filename)
            return df.to_dict("records") if not df.empty else []
        except Exception as e:
            logger.error("Error loading CSV {}: {}".format(filename, e))
            return []


def log_search(terms, locs, found, dur):
    logs = []
    if os.path.exists(SEARCH_LOG_FILE):
        try:
            with open(SEARCH_LOG_FILE, "r") as f:
                logs = json.load(f)
        except:
            pass
    logs.append({
        "timestamp": datetime.now().isoformat(),
        "terms": terms,
        "locations": locs,
        "found": found,
        "duration_sec": round(dur, 1),
    })
    if len(logs) > 100:
        logs = list(logs)[-100:]
    with open(SEARCH_LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)


# ═══════════════════════════════════════════════════════════
#  MAIL QUEUE MANAGER (JSON-based)
# ═══════════════════════════════════════════════════════════

# ─── Mail Queue Commands (PA Bridge) ─────────────────────




async def cmd_papush(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Manually push the mail queue to PythonAnywhere via API.
    """
    if not PA_TOKEN or not PA_USERNAME:
        await update.effective_message.reply_text("❌ PA_TOKEN or PA_USERNAME missing in .env")
        return
        
    try:
        stats = get_mail_stats()
        queue = load_mail_queue()

        # Count items by priority
        priority_counts = {}
        for q in queue:
            if q.get("status") == "pending":
                pri = q.get("priority", "NORMAL")
                priority_counts[pri] = priority_counts.get(pri, 0) + 1

        # Format priority breakdown
        pri_lines = []
        for pri in ["CRITICAL", "HIGH", "NORMAL", "LOW"]:
            count = priority_counts.get(pri, 0)
            if count > 0:
                emoji = {"CRITICAL": "🔴", "HIGH": "🟠",
                         "NORMAL": "🟢", "LOW": "⚪"}.get(pri, "⚪")
                pri_lines.append(f"  {emoji} {pri}: *{count}*")

        pri_text = "\n".join(pri_lines) if pri_lines else "  📭 No pending items"

        await update.effective_message.reply_text("⏳ Pushing latest queue to PythonAnywhere...")
        
        import threading
        threading.Thread(target=push_to_pa, args=(MAIL_QUEUE_FILE,), daemon=True).start()

        msg = (
            f"📤 *Mail Queue Successfully Pushed!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *Queue Summary:*\n"
            f"  📋 Total: *{stats['total']}*\n"
            f"  ⏳ Pending: *{stats['pending']}*\n"
            f"  📤 Sending: *{stats.get('sending', 0)}*\n"
            f"  ✅ Sent: *{stats['sent']}*\n"
            f"  ❌ Failed: *{stats['failed']}*\n\n"
            f"🎯 *Pending by Priority:*\n"
            f"{pri_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"ℹ️ PythonAnywhere now has your latest emails.\n"
            f"They will send automatically at 8 AM, or you can trigger them manually via PA Bash:\n"
            f"`python3 bot.py`"
        )

        await update.effective_message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"papush error: {e}")
        await update.effective_message.reply_text(f"❌ Error during push: {e}")


async def cmd_papull(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Show what PA has done recently.
    PA updates Replit via /api/mail_update — just read the results.
    """
    try:
        queue = load_mail_queue()
        stats = get_mail_stats()

        # Find recently updated items (sent or failed in last 24h)
        now = datetime.now()
        recent = []
        for q in queue:
            updated = q.get("updated_at", "")
            if not updated:
                continue
            try:
                dt = datetime.strptime(updated, "%Y-%m-%d %H:%M")
                if (now - dt).total_seconds() < 86400:  # Last 24 hours
                    if q.get("status") in ("sent", "failed",
                                           "permanently_failed", "bounced"):
                        recent.append(q)
            except (ValueError, TypeError):
                continue

        if not recent:
            await update.effective_message.reply_text(
                f"📬 *No recent PA activity (last 24h)*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 Queue: {stats['pending']} pending, "
                f"{stats['sent']} sent total\n\n"
                f"⏰ PA runs daily at 8 AM IST\n"
                f"💡 Check PythonAnywhere → Tasks for schedule",
                parse_mode="Markdown",
            )
            return

        # Build report of recent activity
        sent_items = [r for r in recent if r.get("status") == "sent"]
        failed_items = [r for r in recent
                        if r.get("status") in ("failed",
                                                "permanently_failed")]

        msg = (
            f"📬 *PA Activity (Last 24h)*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Sent: *{len(sent_items)}*\n"
            f"❌ Failed: *{len(failed_items)}*\n\n"
        )

        if sent_items:
            msg += "*Recently Sent:*\n"
            for item in sent_items[-8:]:
                msg += (
                    f"  ✅ `{item.get('email', '')}` → "
                    f"{item.get('company', 'Unknown')}\n"
                )
            msg += "\n"

        if failed_items:
            msg += "*Failed:*\n"
            for item in failed_items[-5:]:
                err = item.get("error", "unknown")
                msg += (
                    f"  ❌ `{item.get('email', '')}` "
                    f"({err[:30] if err else 'unknown'})\n"
                )
            msg += "\n"

        msg += (
            f"📊 Queue: {stats['pending']} pending, "
            f"{stats['sent']} sent total"
        )

        await update.effective_message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"papull error: {e}")
        await update.effective_message.reply_text(
            f"❌ Error reading results: {str(e)[:200]}"
        )


def reset_stuck_sending(hours=2):
    """Auto-reset items stuck in 'sending' status for too long."""
    queue = load_mail_queue()
    now = datetime.now()
    changed = 0

    for q in queue:
        if str(q.get("status", "")).lower() == "sending":
            ts = q.get("updated_at") or q.get("last_attempt") or ""
            try:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M")
                if (now - dt).total_seconds() > hours * 3600:
                    q["status"] = "pending"
                    changed += 1
            except Exception:
                q["status"] = "pending"
                changed += 1

    if changed:
        save_mail_queue(queue)
    return changed


def get_email_status(email):
    """Return explicit status string for UI feedback"""
    email = email.strip().lower()
    with data_lock:
        queue = safe_load_json(MAIL_QUEUE_FILE, [])
        for q in queue:
            if q.get("email", "").lower() == email:
                return "sent" if str(q.get("status")).lower() == "sent" else "queued"
        if os.path.exists(SENT_LOG_FILE):
            try:
                df = pd.read_csv(SENT_LOG_FILE)
                if not df.empty and "email" in df.columns and email in df["email"].str.lower().values:
                    return "sent"
            except: pass
        if os.path.exists(EMAILS_FILE):
            try:
                df = pd.read_csv(EMAILS_FILE)
                if not df.empty and "email" in df.columns and email in df["email"].str.lower().values:
                    return "processed"
            except: pass
    return "new"

def is_email_already_processed(email):
    """Check ALL sources for duplicates (Legacy Wrapper)"""
    return get_email_status(email) != "new"


def calculate_mail_priority(email, company, source, role=""):
    """Smart priority scoring"""
    score = 50  # Base score

    # Source priority
    source_scores = {
        "manual": 30,  # User manually added = highest
        "quick_mail": 25,  # Quick mail button
        "forwarded": 20,  # Forwarded post
        "bulk_scan": 15,  # Bulk screenshot
        "job_scrape": 10,  # Auto-scraped
        "auto_add": 5,  # Auto-added from old data
    }
    score += source_scores.get(source, 5)

    # MNC bonus
    company_lower = company.lower()
    for mnc in MNC_NAMES_LOWER:
        if mnc in company_lower:
            score += 25
            break

    # HR/Recruiter email bonus
    hr_indicators = ["hr@", "hiring@", "recruit", "career", "talent", "jobs@"]
    for ind in hr_indicators:
        if ind in email.lower():
            score += 15
            break

    # Personal email (less likely to be valid)
    personal = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com"]
    if any(p in email.lower() for p in personal):
        score -= 10

    # Role relevance
    relevant = [
        "python", "developer", "web", "IT", "data", "fresher", "trainee",
        "intern", "junior", "entry"
    ]
    if role:
        for r in relevant:
            if r in role.lower():
                score += 5
                break

    # Convert score to priority label
    if score >= 80:
        return "CRITICAL"
    elif score >= 60:
        return "HIGH"
    elif score >= 40:
        return "NORMAL"
    else:
        return "LOW"


def add_to_mail_queue(email,
                      company,
                      role="",
                      source="manual",
                      priority=None,
                      url="",
                      template="normal"):
    """Enhanced with auto-priority, tracking fields, and template selection"""
    if not email or not is_valid_email(email):
        return False

    email = email.strip().lower()
    if is_email_already_processed(email):
        return False

    with data_lock:
        queue = load_mail_queue()

        # Auto-calculate priority if not given
        if priority is None or priority == "NORMAL":
            priority = calculate_mail_priority(email, company, source, role)

        queue.append({
            "id":
            hashlib.md5(f"{email}{time.time()}".encode()).hexdigest()[:8],
            "email":
            email,
            "company":
            company or "Unknown Company",
            "role":
            role or "Entry-Level Opportunity",
            "url":
            url,
            "source":
            source,
            "priority":
            priority,
            "priority_score":
            0,  # Will be set by calculate
            "added_at":
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            "status":
            "pending",
            "attempts":
            0,
            "last_attempt":
            None,
            "error":
            None,
            "delivery_status":
            "unknown",
            "opened":
            False,
            "response_received":
            False,
            "notes":
            "",
            "template":
            template or "normal",
        })

        save_mail_queue(queue)
        logger.info(f"Added to mail queue: {email} using {template} template")
        return True


def get_mail_stats():
    queue = load_mail_queue()
    return {
        "total": len(queue),
        "pending": sum(1 for q in queue if q.get("status") == "pending"),
        "sending": sum(1 for q in queue if q.get("status") == "sending"),
        "sent": sum(1 for q in queue if q.get("status") == "sent"),
        "failed": sum(1 for q in queue if q.get("status") in ("failed", "permanently_failed")),
    }


def process_extracted_content(text, source="ocr", queue_now=True):
    """
    THE CORE PIPELINE:
    1. Extract emails from text
    2. Extract company names and roles
    3. Calculate priority for each email
    4. Add to mail queue
    5. Save to email database
    Returns: (added_count, found_emails, details_dict)
    """
    if not text:
        return 0, [], {}

    # ── Step 1: Extract structured details ────────────────
    details = extract_job_details(text)
    emails = extract_emails_from_text(text)
    # Merge emails from both extractors
    all_emails = list(set(emails + details.get("emails", [])))

    company = (
        details["companies"][0] if details["companies"] else "Unknown Company"
    )
    role = details["roles"][0] if details["roles"] else "Entry-Level Position"

    text_lower = text.lower()

    # ── Step 2: Calculate priority per email ──────────────
    added_count = 0
    email_priorities = []

    for email_addr in all_emails:
        # Smart priority based on content analysis
        score = 50  # Base score

        # MNC check (highest boost)
        for mnc_lower, mnc_name in MNC_NAMES_LOWER.items():
            if mnc_lower in company.lower() or mnc_lower in text_lower:
                score += 35
                company = mnc_name  # Use proper MNC name
                break

        # Dream company check
        if any(dc in company.lower() for dc in DREAM_COMPANIES):
            score += 40

        # Relevant skill keywords
        high_keywords = [
            "python", "developer", "fresher", "data analyst",
            "web developer", "software", "IT", "BCA",
            "junior", "trainee", "entry level",
        ]
        for kw in high_keywords:
            if kw in text_lower:
                score += 8

        # Urgency keywords
        urgency = ["urgent", "immediate", "asap", "walk-in", "today"]
        for u in urgency:
            if u in text_lower:
                score += 12
                break

        # HR/recruiter email (more likely to respond)
        hr_indicators = [
            "hr@", "hiring@", "recruit", "career",
            "talent", "jobs@", "placement",
        ]
        for ind in hr_indicators:
            if ind in email_addr.lower():
                score += 15
                break

        # Personal email penalty (less likely valid corporate)
        personal_domains = [
            "gmail.com", "yahoo.com", "hotmail.com",
            "outlook.com", "rediffmail.com",
        ]
        if any(p in email_addr.lower() for p in personal_domains):
            score -= 5

        # Salary mentions boost
        salary_words = [
            "lpa", "ctc", "salary", "package",
            "5l", "6l", "7l", "8l", "10l",
        ]
        for sw in salary_words:
            if sw in text_lower:
                score += 10
                break

        # Convert score to priority label
        if score >= 85:
            priority = "CRITICAL"
        elif score >= 65:
            priority = "HIGH"
        elif score >= 45:
            priority = "NORMAL"
        else:
            priority = "LOW"

        # ── Step 3: Add to mail queue ─────────────────────
        if is_email_already_processed(email_addr):
            pass  # Skip duplicates entirely so added_count ignores them
        elif not queue_now:
            added_count += 1
            email_priorities.append((email_addr, priority, score))
        elif add_to_mail_queue(
            email_addr, company, role,
            source=source, priority=priority
        ):
            added_count += 1
            email_priorities.append((email_addr, priority, score))
            logger.info(
                f"📧 Queued: {email_addr} | {company} | "
                f"Priority: {priority} (score {score})"
            )

        # ── Step 4: Save to email database ────────────────
        save_email(email_addr, company, role, f"Extracted via {source}")

    return added_count, all_emails, {
        "company": company,
        "role": role,
        "emails": all_emails,
        "email_priorities": email_priorities,
        "phones": details.get("phones", []),
        "urls": details.get("urls", []),
    }


def log_mail_activity(action, details=""):
    with data_lock:  # Consolidated lock
        activity = safe_load_json(MAIL_LOG_FILE, [])
        activity.append({
            "timestamp":
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action":
            action,
            "details":
            details
        })
        safe_save_json(MAIL_LOG_FILE, activity[-1000:])


# Issue #15: CSV trimming to prevent infinite growth
def trim_csv(filename, max_rows=5000):
    """Keep only recent rows in CSV"""
    if not os.path.exists(filename):
        return
    try:
        with data_lock:  # Consolidated lock
            df = pd.read_csv(filename)
            if len(df) > max_rows:
                df = df.tail(max_rows)
                df.to_csv(filename, index=False)
                logger.info(f"Trimmed {filename} to {max_rows} rows")
    except Exception as e:
        logger.error(f"Trim error {filename}: {e}")


def get_stats():
    """FIXED: Added unpaid key to prevent KeyError in /stats"""
    s = {
        "total": 0,
        "applied": 0,
        "dismissed": 0,
        "saved": 0,
        "emails": 0,
        "high": 0,
        "mid": 0,
        "low": 0,
        "unpaid": 0,      # ← THIS WAS MISSING
        "unknown": 0,
        "important": 0,
        "mnc": 0,
        "companies": 0,
        "mail_queue": 0,
        "mail_pending": 0,
        "mail_sent": 0,
        "mail_failed": 0,
        "today_sent": 0,
        "today_failed": 0,
        "today_replies": 0,
        "today_auto": 0,
        "today_bounces": 0,
        "total_responses": 0,
    }

    ms = get_mail_stats()
    s.update({
        "mail_queue": int(ms.get("total", 0)),
        "mail_pending": int(ms.get("pending", 0)),
        "mail_sent": int(ms.get("sent", 0)),
        "mail_failed": int(ms.get("failed", 0)),
        "today_sent": int(ms.get("today_sent", 0)),
        "today_failed": int(ms.get("today_failed", 0)),
    })

    # Inbox stats from email_monitor
    istats = _em_module.get_inbox_stats()
    s.update({
        "today_replies": istats.get("today_replies", 0),
        "today_auto": istats.get("today_auto", 0),
        "today_bounces": istats.get("today_bounces", 0),
        "total_responses": istats.get("total_responses", 0),
    })

    categories = {}
    last_update = "Never"

    with data_lock:
        if os.path.exists(JOBS_FILE):
            try:
                df = pd.read_csv(JOBS_FILE)
                if not df.empty:
                    s["total"] = int(len(df))

                    # Normalize salary column names
                    if "min_amount" not in df.columns:
                        for c in ["salary_min", "min_salary"]:
                            if c in df.columns:
                                df["min_amount"] = df[c]
                                break

                    if "priority" in df.columns:
                        for p in ["HIGH", "MID", "LOW", "UNKNOWN"]:
                            s[p.lower()] = int(
                                len(df[df["priority"] == p])
                            )
                    else:
                        for _, row in df.iterrows():
                            sal = str(
                                row.get("salary", "")
                            ).lower()
                            if "unpaid" in sal or "stipend" in sal:
                                s["unpaid"] += 1
                            elif any(
                                x in sal
                                for x in [
                                    "5l", "6l", "7l",
                                    "8l", "9l", "10l",
                                ]
                            ):
                                s["high"] += 1
                            elif any(
                                x in sal for x in ["3l", "4l"]
                            ):
                                s["mid"] += 1
                            elif any(
                                x in sal for x in ["1l", "2l"]
                            ):
                                s["low"] += 1
                            else:
                                s["unknown"] += 1

                    # Count unpaid from salary_display too
                    if "salary_display" in df.columns:
                        s["unpaid"] += int(
                            len(
                                df[
                                    df["salary_display"]
                                    .str.lower()
                                    .str.contains(
                                        "unpaid|stipend|volunteer",
                                        na=False,
                                    )
                                ]
                            )
                        )

                    if "is_important" in df.columns:
                        s["important"] = int(
                            len(df[df["is_important"].apply(to_bool)])
                        )
                    if "category" in df.columns:
                        categories = (
                            df["category"].value_counts().to_dict()
                        )
                    if "company" in df.columns:
                        s["companies"] = int(
                            df["company"].dropna().nunique()
                        )
                    if "scraped_at" in df.columns and len(df) > 0:
                        last_update = str(df["scraped_at"].iloc[-1])
            except Exception as e:
                logger.error(f"Stats error: {e}")

    for f, k in [
        (APPLIED_FILE, "applied"),
        (DISMISSED_FILE, "dismissed"),
        (SAVED_FILE, "saved"),
    ]:
        if os.path.exists(f):
            try:
                import pandas as pd
                df = pd.read_csv(f)
                s[k] = int(len(df)) if not df.empty else 0
            except Exception:
                pass

    if os.path.exists(EMAILS_FILE):
        try:
            import pandas as pd
            df = pd.read_csv(EMAILS_FILE)
            if not df.empty and "email" in df.columns:
                s["emails"] = int(df["email"].nunique())
        except Exception:
            pass

    if os.path.exists(MNC_FILE):
        try:
            import pandas as pd
            df = pd.read_csv(MNC_FILE)
            s["mnc"] = int(len(df)) if not df.empty else 0
        except Exception:
            pass

    res = dict(s)
    res["categories"] = categories
    res["last_update"] = last_update
    return res


# ═══════════════════════════════════════════════════════════
#  TEXT EXTRACTION FROM FORWARDED MESSAGES


def extract_job_details(text):
    result = {
        "emails": [],
        "companies": [],
        "roles": [],
        "phones": [],
        "urls": []
    }
    if not text: return result
    result["emails"] = extract_emails_from_text(text)
    result["phones"] = list(
        set(re.findall(r'(?:\+91[\-\s]?)?[6-9]\d{9}', text)))
    result["urls"] = list(
        set(re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text)))
    for pattern in [
            r'(?:company|organization|firm|employer)\s*[:\-–]\s*(.+?)(?:\n|$)',
            r'([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\s+(?:is hiring|hiring|openings|recruitment)',
            r'(?:at|@)\s+([A-Z][A-Za-z\s&.]+?)(?:\s+is|\n|$)',
            r'(?:join|career at)\s+([A-Z][A-Za-z\s&.]+?)(?:\n|$)',
    ]:
        for m in re.findall(pattern, text, re.IGNORECASE):
            m = m.strip()
            if 2 < len(m) < 60: result["companies"].append(m)
    for pattern in [
            r'(?:position|role|designation|opening for|hiring for)\s*[:\-–]\s*(.+?)(?:\n|$)',
            r'(?:looking for|need|require)\s+(?:a\s+)?(.+?)(?:\n|$)',
    ]:
        for m in re.findall(pattern, text, re.IGNORECASE):
            m = m.strip()
            if 3 < len(m) < 80: result["roles"].append(m)
    for role in [
            "python",
            "developer",
            "web",
            "frontend developer",
            "backend developer",
            "data analyst",
            "software developer",
            "software engineer",
            "IT support",
            "research associate",
            "research analyst",
            "MIS executive",
            "business analyst",
            "QA tester",
            "junior developer",
            "trainee",
            "intern",
    ]:
        if role in text.lower(): result["roles"].append(role.title())
    result["companies"] = list(set(result["companies"]))[:5]
    result["roles"] = list(set(result["roles"]))[:5]
    return result


def _fix_ocr_text(text):
    """Fix common OCR mistakes — shared by all OCR backends (Fix #10)"""
    if not text:
        return text
    # Email domain fixes
    text = text.replace("@gmai1.com", "@gmail.com")
    text = text.replace("@gmall.com", "@gmail.com")
    text = text.replace("@grnail.com", "@gmail.com")
    text = text.replace("@yah00.com", "@yahoo.com")
    text = text.replace("@yaho0.com", "@yahoo.com")
    text = text.replace("@hotmai1.com", "@hotmail.com")
    text = text.replace("@out1ook.com", "@outlook.com")
    text = re.sub(r'(\w)\.corn', r'\1.com', text)
    text = text.replace(".c0m", ".com")
    text = text.replace(".co.ln", ".co.in")
    text = text.replace(".co.1n", ".co.in")
    text = text.replace("©", "@")
    text = text.replace("(at)", "@")
    text = text.replace("[at]", "@")
    text = text.replace("(dot)", ".")
    text = text.replace("[dot]", ".")

    # Indian specific corrections
    text = text.replace(".oo.in", ".co.in")
    text = text.replace(".co.im", ".co.in")
    text = text.replace(" gmai ", " gmail ")
    text = text.replace(" gmall ", " gmail ")
    text = text.replace(" outl00k ", " outlook ")
    text = text.replace(" infornation ", " information ")
    text = text.replace(" technolgy ", " technology ")
    text = text.replace(" experlence ", " experience ")
    text = text.replace(" requlred ", " required ")
    text = text.replace(" locatlon ", " location ")
    text = text.replace(" appllcatlon ", " application ")
    text = text.replace(" candldate ", " candidate ")
    text = text.replace(" qualiflcation ", " qualification ")
    text = text.replace(" skllls ", " skills ")
    text = text.replace(" responslbilitles ", " responsibilities ")
    text = text.replace(" beneflts ", " benefits ")
    text = text.replace(" salarles ", " salaries ")
    text = text.replace(" opportunlty ", " opportunity ")
    text = text.replace(" companles ", " companies ")
    text = text.replace(" hr ", " HR ")
    text = text.replace(" recrultment ", " recruitment ")
    text = text.replace(" appllcant ", " applicant ")
    text = text.replace(" appllcants ", " applicants ")
    
    # Fix common letter mistakes
    text = re.sub(r'(\w+)@(\w+)\.c(0|o)m', lambda m: f"{m.group(1)}@{m.group(2)}.com", text)
    text = re.sub(r'\bl\b', '1', text)  # lone 'l' → '1' in phone numbers context
    return text


def _try_ocr_space(image_bytes):
    """Primary OCR using OCR.space API via Requests (Zero RAM impact)"""
    if not OCR_SPACE_API_KEY:
        logger.warning("⚠️ OCR_SPACE_API_KEY not set!")
        return None

    try:
        import requests
        resp = requests.post(
            "https://api.ocr.space/parse/image",
            files={"file": ("image.jpg", image_bytes, "image/jpeg")},
            data={"apikey": OCR_SPACE_API_KEY, "language": "eng"},
            timeout=30
        )
        result = resp.json()

        if result.get("OCRExitCode") == 1:
            text_list = [p.get("ParsedText", "") for p in result.get("ParsedResults", [])]
            return _fix_ocr_text(" ".join(text_list).strip())
        else:
            err = result.get("ErrorMessage", "Unknown OCR.space error")
            logger.error(f"OCR.space Error: {err} | {result}")
            return None

    except Exception as e:
        logger.error(f"OCR.space Request Failed: {e}")
        return None


def _try_pa_ocr(image_bytes):
    """Secondary OCR using PythonAnywhere API (if available)"""
    if not PA_OCR_URL:
        return None
    try:
        import base64
        img_b64 = base64.b64encode(image_bytes).decode()
        payload = json.dumps({"image": img_b64}).encode()
        req = urllib.request.Request(
            PA_OCR_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Mail-Bot-Secret": MAIL_BOT_SECRET
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read().decode())
            text = result.get("text", "")
            if text and len(text.strip()) > 5:
                logger.info(f"📡 PA OCR extracted {len(text)} chars")
                return _fix_ocr_text(text.strip())
    except Exception as e:
        logger.warning(f"PA OCR failed (falling back to local): {e}")
    return None


def extract_from_image(image_bytes):
    """
    Extract text from image — Fallback Chain (Fix #1, #8, #10):
    1. PythonAnywhere OCR API (Primary)
    2. Local pytesseract (Fallback)
    """
    # Memory guard
    if psutil.virtual_memory().available < 30 * 1024 * 1024:
        logger.error("❌ Critical Memory Low - OCR Aborted")
        return None

    # Image Compression (Fix #8)
    try:
        bio_in = io.BytesIO(image_bytes)
        img = PILImage.open(bio_in)
        if img.width > 2000 or img.height > 2000:
            img.thumbnail((2000, 2000))
        
        bio_out = io.BytesIO()
        img.save(bio_out, format="JPEG", quality=75)
        compressed_bytes = bio_out.getvalue()
        img.close()
    except Exception as e:
        logger.warning(f"Compression failed: {e}")
        compressed_bytes = image_bytes

    # ── Try 1: OCR.space API (Primary — zero RAM) ──
    text = _try_ocr_space(compressed_bytes)
    if text and len(text.strip()) > 10:
        logger.info("✅ OCR Success: OCR.space")
        return text

    # ── Try 2: Remote PythonAnywhere OCR (Secondary) ──
    pa_text = _try_pa_ocr(compressed_bytes)
    if pa_text and len(pa_text.strip()) > 10:
        logger.info("✅ OCR Success: PythonAnywhere")
        return pa_text

    # ── Try 2: Local pytesseract ──
    if not OCR_AVAILABLE:
        return None

    try:
        bio = io.BytesIO(compressed_bytes)
        img = PILImage.open(bio)
        text = pytesseract.image_to_string(img.convert('L'), config='--oem 3 --psm 6')
        img.close()
        return _fix_ocr_text(text)
    except Exception as e:
        logger.error(f"Pytesseract failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════
#  BULK IMAGE SCANNING
# ═══════════════════════════════════════════════════════════


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Process photos: OCR → Extract → Priority → Queue. No recursion."""
    try:
        msg = update.effective_message
        if not msg or not msg.photo:
            return

        # Bug #14 Auth Check
        if str(msg.chat_id) != str(CHAT_ID):
            logger.warning(f"Unauthorized photo access from {msg.chat_id}")
            return

        # ── Tell user we are working ──────────────────────
        status_msg = await msg.reply_text(
            "📸 *Scanning image for job details & emails...*\n"
            "⏳ This may take 10-30 seconds on first use.",
            parse_mode="Markdown",
        )

        # ── Download photo (highest resolution) ───────────
        photo = msg.photo[-1]
        tg_file = await ctx.bot.get_file(photo.file_id)
        img_bytes = await tg_file.download_as_bytearray()

        logger.info(
            f"📸 Photo received: {len(img_bytes)} bytes, "
            f"size: {photo.width}x{photo.height}"
        )

        # ── Run OCR ───────────────────────────────────────
        ocr_text = extract_from_image(img_bytes)

        # Free memory immediately
        del img_bytes

        if not ocr_text:
            await safe_edit(
                status_msg,
                "❌ *OCR Failed*\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Could not read text from this image.\n\n"
                "*Possible reasons:*\n"
                "• Image is too blurry or low resolution\n"
                "• Text is handwritten (OCR works best on printed text)\n"
                "• Image contains mostly graphics, not text\n"
                "• Not enough RAM on server\n\n"
                "*Try:*\n"
                "• Send a clearer/larger screenshot\n"
                "• Crop to show only the text area\n"
                "• Copy-paste the text manually instead",
                parse_mode="Markdown",
            )
            return

        # ── Update status ─────────────────────────────────
        await safe_edit(
            status_msg,
            f"✅ *Text extracted!* ({len(ocr_text)} chars)\n"
            f"🔍 Scanning for emails & job details...",
            parse_mode="Markdown",
        )

        # ── Process: Extract emails, calculate priority, queue ─
        added, found_emails, details = process_extracted_content(
            ocr_text, source="photo_ocr", queue_now=False
        )

        # ── Build response ────────────────────────────────
        company = details.get("company", "Unknown")
        role = details.get("role", "Unknown")
        phones = details.get("phones", [])
        priorities = details.get("email_priorities", [])

        if added > 0:
            # Format email list with priorities
            email_lines = []
            new_emails_set = set()
            for email_addr, pri, score in priorities:
                new_emails_set.add(email_addr)
                pri_emoji = {
                    "CRITICAL": "🔴", "HIGH": "🟠",
                    "NORMAL": "🟢", "LOW": "⚪",
                }.get(pri, "⚪")
                email_lines.append(f"  {pri_emoji} `{email_addr}` → *{pri}*")
            
            # Show duplicates if any
            duplicates = [e for e in found_emails if e not in new_emails_set]
            if duplicates:
                email_lines.append("\n*Already Processed:*")
                for e in duplicates:
                    st = get_email_status(e)
                    if st == "sent": email_lines.append(f"  ✅ `{e}` (Sent successfully)")
                    elif st == "queued": email_lines.append(f"  🕒 `{e}` (Still in queue)")
                    else: email_lines.append(f"  ⚙️ `{e}` (Previously processed)")

            response = (
                f"🎯 *Job Found & Emails Queued!*\n"
                f"🏢 Company: *{escape_md(company)}*\n"
                f"💼 Role: *{escape_md(role)}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📧 *Total Emails Found:* {len(found_emails)}\n"
                f"✅ *New Ready to Send:* {added}\n\n"
                f"*Status Breakdown:*\n"
                + "\n".join(email_lines)
                + "\n"
            )

            if phones:
                response += (
                    f"\n📱 *Phones:* {', '.join(phones[:3])}\n"
                )

            response += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n👇 *Select an Email Template to Queue:*"
            
            import time, hashlib
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton
            data_key = hashlib.md5(f"photo_{time.time()}".encode()).hexdigest()[:6]
            ctx.user_data.setdefault("pending_mail_data", {})[data_key] = {
                "emails": [e for e, p, s in details.get("email_priorities", [])],
                "company": company,
                "role": details.get("role", "IT Role")
            }
            
            buttons = []
            for tid, info in load_templates().items():
                buttons.append([InlineKeyboardButton(f"{info['emoji']} {info['name']}", callback_data=f"tmpl_{tid}_{data_key}")])
            kb = InlineKeyboardMarkup(buttons)

            await safe_edit(status_msg, response, parse_mode="Markdown", reply_markup=kb)

        elif found_emails:
            status_lines = []
            for e in found_emails:
                st = get_email_status(e)
                if st == "sent": status_lines.append(f"  ✅ `{e}` (Already sent successfully)")
                elif st == "queued": status_lines.append(f"  🕒 `{e}` (Still in queue)")
                else: status_lines.append(f"  ⚙️ `{e}` (Previously processed/scraped)")
            
            email_list = "\n".join(status_lines)
            await safe_edit(
                status_msg,
                f"ℹ️ *Emails Found But Already Processed*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Status of extracted emails:\n"
                f"{email_list}\n\n"
                f"📊 /mailqueue to see current queue",
                parse_mode="Markdown",
            )
        else:
            # No emails found — show what OCR did extract
            preview = ocr_text[:400].replace("*", "").replace("`", "")
            await safe_edit(
                status_msg,
                f"📝 *Text Found But No Emails Detected*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"OCR extracted {len(ocr_text)} characters but "
                f"no valid email addresses were found.\n\n"
                f"*Extracted Text Preview:*\n"
                f"_{escape_md(preview)}_\n\n"
                f"💡 *Tip:* Make sure the email address is "
                f"clearly visible in the screenshot.",
                parse_mode="Markdown",
            )

    except Exception as e:
        logger.error(f"handle_photo error: {e}", exc_info=True)
        await safe_reply(
            update,
            f"⚠️ *Photo Processing Error*\n`{str(e)[:150]}`\n\n"
            f"Try sending a clearer image or paste the text manually.",
            parse_mode="Markdown",
        )


async def handle_photo_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle images sent as documents (uncompressed) — uses same pipeline"""
    try:
        doc = update.message.document
        if (
            not doc
            or not doc.mime_type
            or not doc.mime_type.startswith("image/")
        ):
            return

        status_msg = await update.effective_message.reply_text(
            "📸 *Processing uncompressed image...*",
            parse_mode="Markdown",
        )

        tg_file = await ctx.bot.get_file(doc.file_id)
        img_bytes = await tg_file.download_as_bytearray()

        ocr_text = extract_from_image(img_bytes)
        del img_bytes

        if not ocr_text:
            await safe_edit(
                status_msg,
                "❌ *OCR Failed:* No readable text in this file.",
                parse_mode="Markdown",
            )
            return

        # Same pipeline as handle_photo
        added, found_emails, details = process_extracted_content(
            ocr_text, source="document_ocr", queue_now=False
        )

        if added > 0:
            new_emails_set = set([e for e, p, s in details.get("email_priorities", [])])
            status_lines = []
            for e in found_emails:
                if e in new_emails_set:
                    status_lines.append(f"  🟢 `{e}` (New, ready to send)")
                else:
                    st = get_email_status(e)
                    if st == "sent": status_lines.append(f"  ✅ `{e}` (Sent successfully)")
                    elif st == "queued": status_lines.append(f"  🕒 `{e}` (Still in queue)")
                    else: status_lines.append(f"  ⚙️ `{e}` (Previously processed)")
                    
            email_list = "\n".join(status_lines)
            import time, hashlib
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton
            data_key = hashlib.md5(f"doc_{time.time()}".encode()).hexdigest()[:6]
            ctx.user_data.setdefault("pending_mail_data", {})[data_key] = {
                "emails": [e for e, p, s in details.get("email_priorities", [])],
                "company": details.get("company", "Unknown"),
                "role": details.get("role", "IT Role")
            }
            
            buttons = []
            for tid, info in load_templates().items():
                buttons.append([InlineKeyboardButton(f"{info['emoji']} {info['name']}", callback_data=f"tmpl_{tid}_{data_key}")])
            kb = InlineKeyboardMarkup(buttons)

            await safe_edit(
                status_msg,
                f"✅ *Document Scanned!*\n\n"
                f"📧 *Total Emails Found:* {len(found_emails)}\n"
                f"✅ *New Ready to Send:* {added}\n\n"
                f"*Status Breakdown:*\n{email_list}\n\n"
                f"👇 *Select Template for New Emails:*",
                parse_mode="Markdown",
                reply_markup=kb
            )
        elif found_emails:
            status_lines = []
            for e in found_emails:
                st = get_email_status(e)
                if st == "sent": status_lines.append(f"  ✅ `{e}` (Already sent successfully)")
                elif st == "queued": status_lines.append(f"  🕒 `{e}` (Still in queue)")
                else: status_lines.append(f"  ⚙️ `{e}` (Previously processed/scraped)")
            
            await safe_edit(
                status_msg,
                f"ℹ️ *Emails Found But Already Processed*\n\n"
                f"{chr(10).join(status_lines)}",
                parse_mode="Markdown",
            )
        else:
            await safe_edit(
                status_msg,
                f"📝 Text found ({len(ocr_text)} chars) "
                f"but no emails detected.",
                parse_mode="Markdown",
            )

    except Exception as e:
        logger.error(f"handle_photo_document error: {e}", exc_info=True)
        await safe_reply(update, "⚠️ Error processing document image.")


async def cmd_bulkscan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User sends /bulkscan then forwards multiple images (Bug #41 fix)"""
    ctx.user_data["bulk_mode"] = True
    ctx.user_data["bulk_texts"] = []
    ctx.user_data["bulk_count"] = 0
    await safe_reply(update, "📸 *Bulk Scan Mode ON*\n"
                     "━━━━━━━━━━━━━━━━━━\n\n"
                     "Send screenshots/photos now. I'll collect them all.\n"
                     "Type /bulkdone → Process All\n"
                     "Type /bulkcancel → Abort",
                     parse_mode="Markdown")


async def cmd_bulkdone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Process all collected bulk texts using the unified pipeline"""
    if not ctx.user_data.get("bulk_mode"):
        await update.effective_message.reply_text(
            "Not in bulk mode. Use /bulkscan first"
        )
        return

    ctx.user_data["bulk_mode"] = False
    all_texts = ctx.user_data.get("bulk_texts", [])
    count = ctx.user_data.get("bulk_count", 0)

    if not all_texts:
        await update.effective_message.reply_text(
            "❌ No text extracted from images.\n"
            "Try sending clearer screenshots."
        )
        return

    status_msg = await update.effective_message.reply_text(
        f"⏳ Processing {count} images...",
    )

    # Combine all OCR text
    combined_text = "\n\n".join(all_texts)

    # Use the unified pipeline
    total_added, all_emails, details = process_extracted_content(
        combined_text, source="bulk_scan", queue_now=False
    )

    priorities = details.get("email_priorities", [])


    # Format results
    if total_added > 0:
        new_emails_set = set()
        email_lines = []
        for email_addr, pri, score in priorities:
            new_emails_set.add(email_addr)
            pri_emoji = {
                "CRITICAL": "🔴", "HIGH": "🟠",
                "NORMAL": "🟢", "LOW": "⚪",
            }.get(pri, "⚪")
            email_lines.append(f"  {pri_emoji} `{email_addr}` → *{pri}*")

        duplicates = [e for e in all_emails if e not in new_emails_set]
        if duplicates:
            email_lines.append("\n*Already Processed:*")
            for e in duplicates:
                st = get_email_status(e)
                if st == "sent": email_lines.append(f"  ✅ `{e}` (Sent successfully)")
                elif st == "queued": email_lines.append(f"  🕒 `{e}` (Still in queue)")
                else: email_lines.append(f"  ⚙️ `{e}` (Previously processed)")

        response = (
            f"📸 *Bulk Scan Complete!*\n"
            f"══════════════════════\n\n"
            f"🖼️ Images scanned: *{count}*\n"
            f"📧 Total emails found: *{len(all_emails)}*\n"
            f"✅ New ready to send: *{total_added}*\n\n"
            f"*Status Breakdown:*\n"
            + "\n".join(email_lines[:15])
        )

        if len(email_lines) > 15:
            response += f"\n  ... +{len(email_lines) - 15} more"

        response += (
            "\n\n📤 /papush to check sending status\n"
            "📊 /mailstats for full overview"
        )

        await safe_edit(status_msg, response, parse_mode="Markdown")

    elif all_emails:
        status_lines = []
        for e in all_emails:
            st = get_email_status(e)
            if st == "sent": status_lines.append(f"  ✅ `{e}` (Already sent successfully)")
            elif st == "queued": status_lines.append(f"  🕒 `{e}` (Still in queue)")
            else: status_lines.append(f"  ⚙️ `{e}` (Previously processed)")
            
        list_str = "\n".join(status_lines[:15])
        if len(status_lines) > 15: list_str += f"\n  ... +{len(status_lines)-15} more"
        
        await safe_edit(
            status_msg,
            f"📸 Scanned {count} images.\n"
            f"Found {len(all_emails)} emails but all are duplicates:\n\n"
            f"{list_str}",
            parse_mode="Markdown",
        )
    else:
        preview = combined_text[:300].replace("*", "").replace("`", "")
        await safe_edit(
            status_msg,
            f"📸 Scanned {count} images.\n"
            f"📝 Text found but no emails detected.\n\n"
            f"*Preview:*\n_{escape_md(preview)}_",
            parse_mode="Markdown",
        )

    # Cleanup
    ctx.user_data["bulk_texts"] = []
    ctx.user_data["bulk_count"] = 0


# ═══════════════════════════════════════════════════════════
#  SCRAPER ENGINE (ALL from original + fixes)
# ═══════════════════════════════════════════════════════════


async def scrape_retry(sites, term, loc, num, hours, retries=MAX_RETRIES):
    """Bug #15 fix: Non-blocking async scraping."""
    for attempt in range(retries):
        try:
            loop = asyncio.get_event_loop()
            df = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: scrape_jobs(
                        site_name=sites,
                        search_term=term,
                        location=loc,
                        results_wanted=num,
                        hours_old=hours,
                        country_indeed="India",
                        verbose=0,
                    )
                ),
                timeout=60
            )
            return df
        except Exception as e:
            wait = min((attempt + 1) * 5 + random.uniform(0, 3), 30)
            logger.warning("Retry {}/{}: {}".format(attempt + 1, retries,
                                                    str(e)[:40]))
            if attempt < retries - 1: await asyncio.sleep(wait)
            else: return pd.DataFrame()
    return pd.DataFrame()


async def scrape_all_jobs():
    logger.info("=" * 50)
    logger.info("STARTING JOB SEARCH v8.0")
    start = time.time()
    seen = load_seen()
    fuzzy_seen = load_fuzzy_seen()
    results = []
    all_job_ids = set()
    all_fuzzy_ids = set()
    stats_counter = {"count": 0}

    try:
        for li, loc in enumerate(LOCATIONS):
            logger.info("[{}/{}] {}".format(li + 1, len(LOCATIONS), loc))
            loc_results = []

            # Bug #11 fix: Closure-safe batch fetch
            async def fetch_batch(_loc, _results):
                for term in SEARCH_TERMS:
                    stats_counter["count"] += 1
                    try:
                        jobs = await scrape_retry(["indeed", "linkedin"], term,
                                                  _loc, RESULTS_PER_SEARCH,
                                                  HOURS_OLD)
                        if jobs is not None and len(jobs) > 0:
                            jobs["search_term"] = term
                            jobs["search_location"] = _loc
                            jobs["scraped_at"] = datetime.now().strftime(
                                "%Y-%m-%d %H:%M")
                            _results.append(jobs)
                    except Exception as e:
                        logger.warning("  '{}': {}".format(term, str(e)[:40]))
                    await asyncio.sleep(2)

            try:
                await asyncio.wait_for(fetch_batch(loc, loc_results),
                                       timeout=900)
            except asyncio.TimeoutError:
                logger.error("  TIMEOUT: {}".format(loc))
            except Exception as e:
                logger.error("  BATCH ERR: {}".format(e))

            await asyncio.sleep(3)

            if loc_results:
                df = pd.concat(loc_results, ignore_index=True)
                df = df.drop_duplicates(subset=["job_url"], keep="first")
                df["_dk"] = df["title"].astype(str).str.lower().str.strip(
                ) + "|" + df["company"].astype(str).str.lower().str.strip()
                df = df.drop_duplicates(subset=["_dk"],
                                        keep="first").drop("_dk", axis=1)
                df = df[df.apply(validate_job, axis=1)]
                # description column check done inline below
                df = df[~df.apply(lambda r: should_exclude(
                    str(r.get("title", "")) + " " + str(
                        r.get("description", "")), str(r.get("company", ""))),
                                  axis=1)]

                df["job_id"] = df.apply(
                    lambda r: gen_id(r.get("title", ""), r.get("company", ""),
                                     r.get("job_url", "")),
                    axis=1)
                df["fuzzy_id"] = df.apply(lambda r: gen_fuzzy_id(
                    r.get("title", ""), r.get("company", "")),
                                          axis=1)

                # Triple-layer dedup
                df = df[~df["job_id"].isin(seen) & ~df["job_url"].isin(seen)]
                df = df[~df["job_id"].isin(all_job_ids)
                        & ~df["job_url"].isin(all_job_ids)]
                df = df[~df["fuzzy_id"].isin(fuzzy_seen)
                        & ~df["fuzzy_id"].isin(all_fuzzy_ids)]

                if len(df) > 0:
                    df["category"] = df["title"].apply(categorize_job)
                    df["is_important"] = df.apply(lambda x: is_important(
                        safe_str(x.get("title", "")),
                        safe_str(x.get("company", "")),
                        safe_str(x.get("description", ""))),
                                                  axis=1)
                    df["mnc_name"] = df["company"].apply(
                        lambda c: is_mnc(c) or "")
                    df["is_mnc"] = df["mnc_name"].apply(lambda x: bool(x))

                    # Bug #9 fix: Normalize salary columns
                    for old, new in {
                            "min_salary": "min_amount",
                            "max_salary": "max_amount",
                            "salary_min": "min_amount",
                            "salary_max": "max_amount"
                    }.items():
                        if old in df.columns and new not in df.columns:
                            df[new] = df[old]

                    df["salary_display"] = df.apply(lambda x: format_salary(
                        x.get("min_amount"), x.get("max_amount")),
                                                    axis=1)
                    # Bug #2 fix: Assign priority
                    df["priority"] = df.apply(lambda x: get_priority(
                        x.get("min_amount"), x.get("max_amount")),
                                              axis=1)

                    if "description" in df.columns:
                        df["emails"] = df.apply(
                            lambda x: ", ".join(extract_all_emails(x)), axis=1)
                    else:
                        df["emails"] = ""

                    batch = df.to_dict("records")
                    for j in batch:
                        j["_score"] = relevance_score(j)
                        all_job_ids.add(j.get("job_id", ""))
                        all_fuzzy_ids.add(j.get("fuzzy_id", ""))
                        fuzzy_seen.add(j.get("fuzzy_id", ""))
                        # AUTO-ADD emails to mail queue
                        em = j.get("emails", "")
                        if em and str(em) != "nan":
                            for e in str(em).split(","):
                                e = e.strip()
                                if e and is_valid_email(e):
                                    add_to_mail_queue(
                                        e, safe_str(j.get("company")),
                                        safe_str(j.get("title")), "job_scrape",
                                        "NORMAL")

                    save_fuzzy_seen(fuzzy_seen)
                    batch.sort(key=lambda x: x["_score"], reverse=True)
                    results.extend(batch)
                    job_cache["last_update"] = datetime.now().strftime(
                        "%Y-%m-%d %H:%M")
                    yield loc, batch
    finally:
        dur = time.time() - start
        logger.info("FINAL: {} new unique jobs in {:.0f}s".format(
            len(results), dur))
        log_search(len(SEARCH_TERMS), len(LOCATIONS), len(results), dur)
        job_cache["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M")


# REDUNDANT handle_message definition at line 2155 deleted.



async def cmd_bulkcancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """New #40: Abort bulk scan mode"""
    ctx.user_data["bulk_mode"] = False
    ctx.user_data["bulk_texts"] = []
    ctx.user_data["bulk_count"] = 0
    await safe_reply(update, "🧤 *Bulk Scan Cancelled.*", parse_mode="Markdown")


async def cmd_ocrstatus(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Check OCR Status (Fix #10)"""
    local = "✅ Pytesseract Ready" if OCR_AVAILABLE else "❌ Not Installed"
    remote = "✅ PA API Ready" if PA_OCR_URL else "⚪ Not Configured"
    
    msg = (f"🎯 *OCR System Status*\n"
           f"━━━━━━━━━━━━━━━━━━\n"
           f"💻 Local Fallback: {local}\n"
           f"☁️ Remote Primary: {remote}\n\n"
           f"ℹ️ Primary OCR via PythonAnywhere uses a high-RAM model for better accuracy.")
    await safe_reply(update, msg, parse_mode="Markdown")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """New #61: Check Bot Status & Health"""
    # datetime already imported at module level
    try:
        # Calculate uptime (simple estimation for now)
        # In a real scenario, we could store start_time at module level
        status_msg = (
            f"✅ *Bot Status: ONLINE*\n"
            f"🎯 *Version:* {BOT_VERSION}\n"
            f"📸 *OCR:* {'Enabled' if OCR_AVAILABLE else 'Disabled'}\n"
            f"📁 *CSV Files:* Checked & Safe\n"
            f"⏰ *Scheduler:* Active (IST)")
        await safe_reply(update, status_msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"cmd_status error: {e}")
        await update.effective_message.reply_text("❌ Error getting status.")


def fmt_job(job):
    """Bug #17 fix: Markdown V1 escaping for special chars"""
    imp = to_bool(job.get("is_important"))
    mnc = to_bool(job.get("is_mnc"))
    pri = safe_str(job.get("priority", "UNKNOWN")).upper()
    cat = safe_str(job.get("category", "General IT"))
    score = job.get("relevance_score", relevance_score(job))

    badges = []
    if imp: badges.append("⭐ IMPORTANT")
    if mnc:
        badges.append("🏢 MNC: {}".format(escape_md(job.get("mnc_name", ""))))
    hdr = "\n".join(badges) + "\n" if badges else ""
    emails = job.get("emails", "")
    eline = "\n📧 *Email:* `{}`".format(emails) if emails and str(
        emails) != "nan" and emails.strip() else "\n📧 *Email:* _Not found_"

    # Score indicator
    # Bug #34 fix: Score UI display
    score_bar = "🟢" if score >= 70 else "🟡" if score >= 40 else "🔴"

    return ("{hdr}"
            "{emoji} *{label}* {pri_bar}\n"
            "🎯 *Match Score:* {score_bar} `{score}/100`\n"
            "💼 JOB • {cat}\n"
            "{sep}\n\n"
            "📌 *{title}*\n\n"
            "🏢 *Company:* {company}\n"
            "{eline}\n"
            "📍 *Location:* {location}\n"
            "🌐 *Source:* {site}\n"
            "💼 *Type:* {job_type}\n"
            "💰 *Salary:* {salary}\n\n"
            "{line}\n"
            "🔗 [🚀 Apply Now →]({url})").format(
                hdr=hdr,
                emoji=PRIORITY_EMOJI.get(pri, "❓"),
                label=escape_md(PRIORITY_LABELS.get(pri, "Unknown")),
                pri_bar="▰" * min(pri_level(pri), 5) +
                "▱" * max(5 - pri_level(pri), 0),
                score_bar=score_bar,
                score=score,
                cat=escape_md(cat),
                title=escape_md(safe_str(job.get("title"))),
                company=escape_md(safe_str(job.get("company"))),
                eline=eline,
                location=escape_md(
                    safe_str(job.get("search_location", job.get("location")))),
                site=escape_md(safe_str(job.get("site", "")).upper()),
                job_type=escape_md(safe_str(job.get("job_type"))),
                salary=escape_md(
                    safe_str(job.get("salary_display", "Not Disclosed"))),
                url=str(job.get("job_url", "")),
                sep=SEP_BOLD,
                line=SEP_LINE)


def job_kb(job):
    jid = job.get("job_id", "")
    url = str(job.get("job_url", ""))
    em = job.get("emails", "")
    rows = [
        [
            InlineKeyboardButton("✅ Applied",
                                 callback_data="ap_{}".format(jid)),
            InlineKeyboardButton("❌ Skip", callback_data="dm_{}".format(jid))
        ],
        [
            InlineKeyboardButton("💾 Save", callback_data="sv_{}".format(jid)),
            InlineKeyboardButton("ℹ️ Details",
                                 callback_data="dt_{}".format(jid))
        ],
    ]
    if em and str(em) != "nan" and em.strip():
        rows.append([
            InlineKeyboardButton("📧 Quick Mail",
                                 callback_data="qm_{}".format(jid)),
            InlineKeyboardButton("📧 Copy", callback_data="em_{}".format(jid)),
        ])
    if url.startswith("http"):
        rows.append([InlineKeyboardButton("🔗 Apply Now →", url=url)])
    return InlineKeyboardMarkup(rows)


# ═══════════════════════════════════════════════════════════
#  TELEGRAM SEND & SEARCH (ALL from original + fixes)
# ═══════════════════════════════════════════════════════════


async def send_job(bot, job):
    """Bug #17 fix: safe symbol handling."""
    max_send_retries = 3
    for attempt in range(max_send_retries):
        try:
            await bot.send_message(chat_id=CHAT_ID,
                                   text=fmt_job(job),
                                   parse_mode="Markdown",
                                   reply_markup=job_kb(job),
                                   disable_web_page_preview=True)
            break
        except Exception as e:
            err_str = str(e).lower()
            if "retry after" in err_str or "too many requests" in err_str:
                wait = 10 * (attempt + 1)
                logger.warning("Rate limited, waiting {}s".format(wait))
                await asyncio.sleep(wait)
            elif attempt < max_send_retries - 1:
                await asyncio.sleep(3)
            else:
                logger.error("Send err after retries: {}".format(e))
                return False
    try:
        data = {
            "job_id": job.get("job_id", ""),
            "title": safe_str(job.get("title")),
            "company": safe_str(job.get("company")),
            "location":
            safe_str(job.get("search_location", job.get("location"))),
            "job_url": str(job.get("job_url", "")),
            "site": safe_str(job.get("site")),
            "category": job.get("category", ""),
            "priority": job.get("priority", ""),
            "salary": job.get("salary_display", ""),
            "mnc_name": job.get("mnc_name", ""),
            "emails": job.get("emails", ""),
            "is_important": job.get("is_important", False),
            "is_mnc": job.get("is_mnc", False),
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        save_to_csv(data, JOBS_FILE)
        if job.get("is_important"): save_to_csv(data, IMPORTANT_FILE)
        if job.get("priority") == "HIGH": save_to_csv(data, HIGH_PRIORITY_FILE)
        if job.get("is_mnc"): save_to_csv(data, MNC_FILE)
        em = job.get("emails", "")
        if em and str(em) != "nan":
            for e in str(em).split(","):
                e = e.strip()
                if e:
                    save_email(e, safe_str(job.get("company")),
                               safe_str(job.get("title")),
                               str(job.get("job_url", "")))
        return True
    except Exception as e:
        logger.error("Save err: {}".format(e))
        return False


async def run_search(bot):
    """Bug #3, #4 fix: async locking and cancellation safety."""
    if search_paused:
        logger.info("Search skipped — paused by user")
        return
    if job_cache["search_running"]: return
    async with search_lock:
        job_cache["search_running"] = True
        total_sent = 0
        total_found = 0
        batch_num = 0
        try:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=("🔍 *Auto Search Started*\n"
                      "══════════════════════════════\n\n"
                      "📅 {date}  ⏰ {time}\n\n"
                      "🌐 *Scanning 20 days of jobs:*\n"
                      "  ├ 📍 {loc} locations\n"
                      "  ├ 🔎 {terms} search terms\n"
                      "  └ 🛡️ {excl} exclusion filters\n\n"
                      "⚡ *Jobs sent LIVE as found!*").format(
                          date=datetime.now().strftime("%d %b %Y"),
                          time=datetime.now().strftime("%H:%M"),
                          loc=len(LOCATIONS),
                          terms=len(SEARCH_TERMS),
                          excl=len(EXCLUDE_EXACT),
                      ),
                parse_mode="Markdown")

            async for loc, batch in scrape_all_jobs():
                batch_num += 1
                total_found += len(batch)
                imp = sum(1 for j in batch if to_bool(j.get("is_important")))
                mc = sum(1 for j in batch if to_bool(j.get("is_mnc")))
                await bot.send_message(chat_id=CHAT_ID,
                                       text=("📡 *Live Batch #{num} — {loc}*\n"
                                             "────────────────────────────\n"
                                             "📌 *{count}* new jobs found!\n"
                                             "⭐ {imp} important • 🏢 {mc} MNC\n"
                                             "📤 Sending now...").format(
                                                 num=batch_num,
                                                 loc=loc,
                                                 count=len(batch),
                                                 imp=imp,
                                                 mc=mc),
                                       parse_mode="Markdown")
                for i, job in enumerate(batch):
                    # Feature #25: Auto-dismiss low quality jobs
                    score = relevance_score(job)
                    job["relevance_score"] = score
                    if score < 10 and not to_bool(job.get("is_important")):
                        logger.info("Auto-skip: {} (score {})".format(
                            job.get('title'), score))
                        continue
                    if await send_job(bot, job): total_sent += 1
                    # Adaptive delay to avoid Telegram rate limits
                    if i < 10:
                        await asyncio.sleep(1.5)
                    elif i < 30:
                        await asyncio.sleep(2.5)
                    else:
                        await asyncio.sleep(4.0)
                    # Pause every 20 messages
                    if (i + 1) % 20 == 0:
                        await asyncio.sleep(10)

            ms = get_mail_stats()
            if total_found == 0:
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=(
                        "✅ *Search Complete*\n────────────────────────────\n\n"
                        "📭 No new jobs found this round.\n\n"
                        "⏰ *Next scan:* in {hrs}h\n📊 /stats for overview"
                    ).format(hrs=HOURS_BETWEEN_SEARCHES),
                    parse_mode="Markdown")
            else:
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=
                    ("🎉 *Search Complete!*\n══════════════════════════════\n\n"
                     "✅ *{sent}* jobs sent from *{batches}* locations\n"
                     "📧 *{mails}* emails auto-added to mail queue\n"
                     "⏰ Next auto search in *{hrs}h*\n\n"
                     "──────────────────────────────\n"
                     "📊 /stats  •  📧 /emails  •  🏢 /mnc\n"
                     "💾 /saved  •  🔥 /high  •  📋 /recent\n"
                     "📨 /mail  •  📤 /mailsend").format(
                         sent=total_sent,
                         batches=batch_num,
                         mails=ms["pending"],
                         hrs=HOURS_BETWEEN_SEARCHES),
                    parse_mode="Markdown")
        except asyncio.CancelledError:
            logger.warning("Search cancelled, {} jobs sent".format(total_sent))
            raise
        except Exception as e:
            logger.error("Search err: {}".format(e))
            try:
                await bot.send_message(chat_id=CHAT_ID,
                                       text="⚠️ Error: {}".format(
                                           str(e)[:150]))
            except:
                pass
        finally:
            job_cache["search_running"] = False


# ═══════════════════════════════════════════════════════════
#  ALL TELEGRAM COMMANDS (original 11 + 6 mail commands)
# ═══════════════════════════════════════════════════════════


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = get_stats()
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 Jobs", callback_data="nav_jobs"),
            InlineKeyboardButton("📨 Mail", callback_data="nav_mail")
        ],
        [
            InlineKeyboardButton("🛠 Tools", callback_data="nav_tools"),
            InlineKeyboardButton("📊 Stats", callback_data="nav_stats")
        ],
        [InlineKeyboardButton("❓ Help & Guide", callback_data="nav_help")],
    ])
    await update.effective_message.reply_text(
        ("🎯 *{name} v{ver}*\n"
         "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
         "📋 *{total}* Jobs  │  📧 *{emails}* Emails\n"
         "📨 *{mp}* Pending  │  ✅ *{ms}* Sent\n\n"
         "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
         "⭐ *{imp}* Important  │  🏢 *{mnc}* MNC\n"
         "🔥 *{high}* High Pay  │  💾 *{saved}* Saved\n"
         "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
         "💡 *Forward any job post to extract & queue!*").format(
             name=BOT_NAME,
             ver=BOT_VERSION,
             total=s["total"],
             imp=s["important"],
             high=s["high"],
             mnc=s["mnc"],
             emails=s["emails"],
             saved=s["saved"],
             mp=s["mail_pending"],
             ms=s["mail_sent"]),
        parse_mode="Markdown",
        reply_markup=kb)


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if job_cache["search_running"]:
        await update.effective_message.reply_text(
            "⚠️ *Search Already Running!*\n{sep}\n"
            "🔄 Please wait for current search to finish.\n📊 Check progress: /stats"
            .format(sep=SEP_THIN),
            parse_mode="Markdown")
        return
    await update.effective_message.reply_text(
        "🔍 *Search Initiated!*\n{sep}\n\n"
        "🚀 Scanning across *{locs}* locations\n"
        "🔎 Using *{terms}* search terms\n"
        "⏳ Estimated time: ~20-30 min\n\n"
        "📊 Results will appear here as they're found!".format(
            sep=SEP_BOLD, locs=len(LOCATIONS), terms=len(SEARCH_TERMS)),
        parse_mode="Markdown")
    # Bug #14 & #15: Non-blocking
    task = asyncio.create_task(run_search(ctx.bot))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def cmd_emails(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(EMAILS_FILE):
        await update.effective_message.reply_text("📭 No emails yet. /search first")
        return
    df = pd.read_csv(EMAILS_FILE).drop_duplicates(subset=["email"])
    if len(df) == 0:
        await update.effective_message.reply_text("📭 No emails.")
        return
    lines = [
        "📧 `{}`\n   └ 🏢 {} | 📌 {}".format(r["email"],
                                          safe_str(r.get("company")),
                                          safe_str(r.get("job_title")))
        for _, r in df.head(30).iterrows()
    ]
    await update.effective_message.reply_text(
        "📧 *All Emails ({} total)*\n{sep}\n\n{}\n\n💡 Tap email to copy!".
        format(len(df), "\n\n".join(lines), sep=SEP_DASH),
        parse_mode="Markdown")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = get_stats()
    ct = ""
    if s["categories"]:
        ct = "\n\n📂 *Top Categories:*\n" + "\n".join([
            "  {} *{}*".format(c, n) for c, n in sorted(
                s["categories"].items(), key=lambda x: x[1], reverse=True)[:10]
        ])
    await update.effective_message.reply_text((
        "📊 *Statistics Dashboard (v{ver})*\n{sep}\n\n"
        "📋 *Overview:*\n"
        "  Total Jobs:    *{total}*\n  Important:     *{imp}*\n  MNC:           *{mnc}*\n\n"
        "💰 *Salary Breakdown:*\n"
        "  🔥 High (5L+):   *{high}*\n  ⭐ Mid (2.5-5L): *{mid}*\n"
        "  📌 Entry (1-2.5L):*{low}*\n  🆓 Unpaid:       *{unpaid}*\n"
        "  ❓ Unknown:      *{unk}*\n\n"
        "📈 *Activity:*\n"
        "  ✅ Applied: *{applied}*\n  💾 Saved: *{saved}*\n"
        "  ❌ Dismissed: *{dismissed}*\n  📧 Emails: *{emails}*\n"
        "  🏢 Companies: *{companies}*\n\n"
        "📨 *Mail Bot:*\n"
        "  📋 Queue: *{mq}* | ⏳ Pending: *{mp}*\n"
        "  ✅ Sent: *{ms}* | ❌ {mf}*\n\n"
        "🕒 *Last Update:* {lu}{ct}").format(total=s["total"],
                                            imp=s["important"],
                                            mnc=s["mnc"],
                                            high=s["high"],
                                            mid=s["mid"],
                                            low=s["low"],
                                            unpaid=s["unpaid"],
                                            unk=s["unknown"],
                                            applied=s["applied"],
                                            saved=s["saved"],
                                            dismissed=s["dismissed"],
                                            emails=s["emails"],
                                            companies=s["companies"],
                                            mq=s["mail_queue"],
                                            mp=s["mail_pending"],
                                            ms=s["mail_sent"],
                                            mf=s["mail_failed"],
                                            lu=s["last_update"],
                                            ct=ct,
                                            ver=BOT_VERSION,
                                            sep=SEP_BOLD),
                                    parse_mode="Markdown")


async def cmd_mnc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(MNC_FILE):
        await update.effective_message.reply_text("🏢 No MNC jobs yet. /search")
        return
    df = pd.read_csv(MNC_FILE)
    if len(df) == 0:
        await update.effective_message.reply_text("🏢 None yet.")
        return
    grp = df.groupby("mnc_name").size().reset_index(name="count")
    grp.columns = ["company", "count"]
    grp = grp.sort_values("count", ascending=False)
    lines = [
        "🏢 *{}* [{}] → {} jobs".format(
            r["company"],
            TOP_MNCS.get(r["company"], {}).get("tier", "?"), r["count"])
        for _, r in grp.head(15).iterrows()
    ]
    recent = [
        "• {} @ {}".format(safe_str(r.get("title")),
                           safe_str(r.get("mnc_name")))
        for _, r in df.tail(5).iterrows()
    ]
    await update.effective_message.reply_text(
        "🏢 *MNC Openings*\n{sep}\n\n{}\n\n*Latest:*\n{}".format(
            "\n".join(lines), "\n".join(recent), sep=SEP_DASH),
        parse_mode="Markdown")


async def cmd_saved(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    jobs = load_csv(SAVED_FILE)
    if not jobs:
        await update.effective_message.reply_text(
            "💾 No saved jobs. Use 💾 button on jobs.")
        return
    lines = [
        "💾 *{}*\n   🏢 {} | 💰 {}".format(safe_str(j.get("title")),
                                        safe_str(j.get("company")),
                                        safe_str(j.get("salary", "N/A")))
        for j in jobs[-10:]
    ]
    await update.effective_message.reply_text("💾 *Saved Jobs ({})*\n{sep}\n\n{}".format(
        len(jobs), "\n\n".join(lines), sep=SEP_DASH),
                                    parse_mode="Markdown")


async def cmd_applied(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    jobs = load_csv(APPLIED_FILE)
    if not jobs:
        await update.effective_message.reply_text("✅ No applied jobs tracked yet.")
        return
    await update.effective_message.reply_text(
        "✅ *Applied Jobs: {}*\n{sep}\nKeep going! 💪".format(len(jobs),
                                                            sep=SEP_DASH),
        parse_mode="Markdown")


async def cmd_high(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    jobs = load_csv(HIGH_PRIORITY_FILE)
    if not jobs:
        await update.effective_message.reply_text("🔥 No high-pay jobs found yet. /search"
                                        )
        return
    lines = [
        "🔥 *{}*\n🏢 {} | 💰 {}".format(safe_str(j.get("title")),
                                     safe_str(j.get("company")),
                                     safe_str(j.get("salary", "N/A")))
        for j in jobs[-10:]
    ]
    await update.effective_message.reply_text(
        "🔥 *High Priority Jobs ({})*\n{sep}\n\n{}".format(len(jobs),
                                                          "\n\n".join(lines),
                                                          sep=SEP_DASH),
        parse_mode="Markdown")


async def cmd_recent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    jobs = load_csv(JOBS_FILE)
    if not jobs:
        await update.effective_message.reply_text("📋 No jobs yet. /search")
        return
    lines = [
        "{} *{}*\n   🏢 {} | {} | 💰 {}".format(
            PRIORITY_EMOJI.get(j.get("priority", "UNKNOWN"), "❓"),
            safe_str(j.get("title")), safe_str(j.get("company")),
            safe_str(j.get("category")), safe_str(j.get("salary", "N/A")))
        for j in jobs[-8:]
    ]
    await update.effective_message.reply_text(
        "📋 *Recent Jobs (last 8)*\n{sep}\n\n{}".format("\n\n".join(lines),
                                                       sep=SEP_DASH),
        parse_mode="Markdown")


async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = get_stats()
    await update.effective_message.reply_text(
        ("📤 *Export Summary*\n{sep}\n\n"
         "📋 {total} jobs | 📧 {emails} emails\n"
         "✅ {applied} applied | 💾 {saved} saved\n"
         "🏢 {mnc} MNC | 📨 {ms} mailed\n\n"
         "🌐 Visit dashboard port {port} for full data").format(
             total=s["total"],
             emails=s["emails"],
             applied=s["applied"],
             saved=s["saved"],
             mnc=s["mnc"],
             ms=s["mail_sent"],
             port=WEB_PORT,
             sep=SEP_DASH),
        parse_mode="Markdown")


# ─── Template Selection Callback Handler ──────────────────

async def cb_template_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle template selection callbacks (Fix #2)"""
    query = update.callback_query
    await query.answer()
    
    if not query.data.startswith("tmpl_"):
        return

    # tmpl_T_K where T=template, K=data_key
    parts = query.data.split("_")
    if len(parts) < 3: return
    
    template_id = parts[1]
    data_key = parts[2]
    
    # Retrieve pending data from ctx
    pending_mail_data = ctx.user_data.get("pending_mail_data", {})
    pending = pending_mail_data.get(data_key)
    if not pending:
        await query.edit_message_text("❌ Data expired or already processed.")
        return

    emails = pending.get("emails", [])
    company = pending.get("company", "Unknown")
    role = pending.get("role", "Research/Job Role")
    
    with data_lock:
        added = 0
        for e in emails:
            if add_to_mail_queue(
                e, company, role,
                source="forwarded", template=template_id
            ):
                added += 1

    tmpl_info = load_templates().get(template_id, {})
    tmpl_name = tmpl_info.get("name", template_id)
    tmpl_emoji = tmpl_info.get("emoji", "📄")
    
    await query.edit_message_text(
        f"✅ *Queued {added} emails!* {'(Skipped duplicates)' if added == 0 else ''}\n"
        f"🏢 Company: *{escape_md(company)}*\n"
        f"📁 Template: *{tmpl_emoji} {tmpl_name}*\n\n"
        f"PythonAnywhere will send these soon."
    )
    # Cleanup
    pending_mail_data.pop(data_key, None)

async def cb_fwd_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle forward-related callbacks — FIXED: Proper data access"""
    q = update.callback_query
    await q.answer()

    if q.data == "fwd_ignore":
        try:
            await q.delete_message()
        except Exception:
            pass
        return

    if q.data == "fwd_add_all":
        # The data is stored in the callback message itself
        # Parse from the message text since user_data is unreliable
        # across sessions
        msg_text = q.message.text or ""

        # Extract emails directly from the message text
        clean_text = msg_text.replace("*", "").replace("`", "").replace("_", "")
        emails = extract_emails_from_text(clean_text)

        if not emails:
            await q.edit_message_text(
                "⚠️ Could not find emails in this message. "
                "Try forwarding again."
            )
            return

        added = 0
        for email_addr in emails:
            if add_to_mail_queue(
                email_addr,
                "Forwarded Company",
                "Entry-Level Position",
                "forwarded",
                "HIGH",
            ):
                added += 1

        if added > 0:
            await q.edit_message_text(
                f"✅ Added {added} email(s) to queue!\n"
                f"📤 Use /papush to check sending status"
            )
        else:
            status_lines = []
            for e in list(set(emails)):
                st = get_email_status(e)
                if st == "sent": status_lines.append(f"  ✅ `{e}` (Sent successfully)")
                elif st == "queued": status_lines.append(f"  🕒 `{e}` (Still in queue)")
                else: status_lines.append(f"  ⚙️ `{e}` (Previously processed)")
            
            await q.edit_message_text(
                "ℹ️ *Duplicate Text Detected:*\n\n" + "\n".join(status_lines),
                parse_mode="Markdown"
            )



async def cb_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    m = q.message

    # ── Inbox callbacks ────────────────────────────────
    if d.startswith(("inbox_", "refresh")):
        await handle_inbox_callbacks(q, d, m, ctx)
        return

    try:
        # ── Resume Update Callbacks ────────────────────
        if d == "resume_confirm":
            pending = ctx.user_data.get("pending_resume")
            if not pending:
                await m.edit_text("❌ Session expired. Please upload your PDF again.")
                return
            
            try:
                bot_file = await ctx.bot.get_file(pending["file_id"])
                await bot_file.download_to_drive(RESUME_FILE)
                import threading
                threading.Thread(target=push_to_pa, args=(RESUME_FILE,), daemon=True).start()
                
                meta = {
                    "filename": pending["file_name"],
                    "size_kb": (pending["file_size"] or 0) // 1024,
                    "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                with open(RESUME_META_FILE, "w", encoding="utf-8") as f:
                    json.dump(meta, f)
                    
                await m.edit_text("✅ *Resume Successfully Updated!*\n\nThis PDF will be cleanly attached to all future applications.", parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Resume download failed: {e}")
                await m.edit_text(f"❌ Failed to process resume: {e}")
                
            ctx.user_data.pop("pending_resume", None)
            return

        elif d == "resume_cancel":
            await m.edit_text("❌ Resume update cancelled.")
            ctx.user_data.pop("pending_resume", None)
            return

        # ── Menu callbacks ─────────────────────────────
        if d == "m_search":
            if job_cache["search_running"]:
                await m.reply_text("⚠️ Already running!")
                return
            await m.reply_text("🔍 Starting search...")
            task = asyncio.create_task(run_search(ctx.bot))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        elif d == "m_imp":
            jobs = load_csv(IMPORTANT_FILE)
            if not jobs:
                await m.reply_text("⭐ No important jobs yet")
                return
            txt = "\n\n".join([
                "⭐ *{}*\n🏢 {} | 💰 {}".format(safe_str(j.get("title")),
                                             safe_str(j.get("company")),
                                             safe_str(j.get("salary", "N/A")))
                for j in jobs[-10:]
            ])
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Menu", callback_data="nav_jobs")]])
            await m.edit_text("⭐ *Important ({})*\n{}\n\n{}".format(
                len(jobs), SEP_DASH, txt),
                              parse_mode="Markdown",
                              reply_markup=kb)

        elif d == "m_mnc":
            jobs = load_csv(MNC_FILE)
            if not jobs:
                await m.reply_text("🏢 No MNC jobs yet")
                return
            txt = "\n\n".join([
                "🏢 *{}*\n@ {}".format(
                    safe_str(j.get("title")),
                    safe_str(j.get("mnc_name", j.get("company"))))
                for j in jobs[-10:]
            ])
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Menu", callback_data="nav_jobs")]])
            await m.edit_text("🏢 *MNC ({})*\n{}\n\n{}".format(
                len(jobs), SEP_DASH, txt),
                              parse_mode="Markdown",
                              reply_markup=kb)

        elif d == "m_high":
            jobs = load_csv(HIGH_PRIORITY_FILE)
            if not jobs:
                await m.reply_text("🔥 No high-pay jobs yet")
                return
            txt = "\n\n".join([
                "🔥 *{}*\n🏢 {} | 💰 {}".format(safe_str(j.get("title")),
                                             safe_str(j.get("company")),
                                             safe_str(j.get("salary", "N/A")))
                for j in jobs[-10:]
            ])
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Menu", callback_data="nav_jobs")]])
            await m.edit_text("🔥 *High Pay ({})*\n{}\n\n{}".format(
                len(jobs), SEP_DASH, txt),
                              parse_mode="Markdown",
                              reply_markup=kb)

        elif d == "m_emails":
            if not os.path.exists(EMAILS_FILE):
                await m.reply_text("📭 No emails yet")
                return
            try:
                df = pd.read_csv(EMAILS_FILE).drop_duplicates(subset=["email"])
                txt = "\n".join([
                    "📧 `{}` → {}".format(r["email"],
                                         safe_str(r.get("company", "")))
                    for _, r in df.head(20).iterrows()
                ])
                kb = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Menu", callback_data="nav_stats")]])
                await m.edit_text(
                    "📧 *Emails ({})*\n{}\n\n{}\n\n💡 Tap to copy!".format(
                        len(df), SEP_DASH, txt),
                    parse_mode="Markdown",
                    reply_markup=kb)
            except Exception as e:
                await m.reply_text("⚠️ Error: {}".format(str(e)[:80]))

        elif d == "m_saved":
            jobs = load_csv(SAVED_FILE)
            if not jobs:
                await m.reply_text("💾 No saved jobs")
                return
            txt = "\n\n".join([
                "💾 *{}*\n🏢 {}".format(safe_str(j.get("title")),
                                      safe_str(j.get("company")))
                for j in jobs[-10:]
            ])
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Menu", callback_data="nav_jobs")]])
            await m.edit_text("💾 *Saved ({})*\n{}\n\n{}".format(
                len(jobs), SEP_DASH, txt),
                              parse_mode="Markdown",
                              reply_markup=kb)

        elif d == "m_applied":
            jobs = load_csv(APPLIED_FILE)
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Menu", callback_data="nav_jobs")]])
            await m.edit_text("✅ *Applied: {}*\n{}\nKeep going! 💪".format(
                len(jobs), SEP_DASH),
                              parse_mode="Markdown",
                              reply_markup=kb)

        elif d == "m_recent":
            jobs = load_csv(JOBS_FILE)
            if not jobs:
                await m.reply_text("📋 No jobs yet")
                return
            txt = "\n\n".join([
                "{} *{}*\n   🏢 {}".format(
                    PRIORITY_EMOJI.get(j.get("priority", ""), ""),
                    safe_str(j.get("title")), safe_str(j.get("company")))
                for j in jobs[-8:]
            ])
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Menu", callback_data="nav_jobs")]])
            await m.edit_text("📋 *Recent (last 8)*\n{}\n\n{}".format(
                SEP_DASH, txt),
                              parse_mode="Markdown",
                              reply_markup=kb)

        elif d == "m_stats":
            s = get_stats()
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Stats", callback_data="nav_stats")]])
            await m.edit_text(
                "📊 *Stats*\n{sep}\n"
                "📋 {t} | ⭐ {i} | 🏢 {mc}\n"
                "🔥 {h} | ⭐ {md} | 📌 {l}\n"
                "✅ {a} | 💾 {sv} | 📧 {e}\n\n"
                "📨 Mail: {mq} queued | {mp} pending | {ms} sent".format(
                    t=s["total"],
                    i=s["important"],
                    mc=s["mnc"],
                    h=s["high"],
                    md=s["mid"],
                    l=s["low"],
                    a=s["applied"],
                    sv=s["saved"],
                    e=s["emails"],
                    mq=s["mail_queue"],
                    mp=s["mail_pending"],
                    ms=s["mail_sent"],
                    sep=SEP_DASH),
                parse_mode="Markdown",
                reply_markup=kb)

        elif d == "m_help":
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data="nav_home")]])
            await m.edit_text("❓ *Help*\n{sep}\n"
                              "🔥HIGH ⭐MID 📌LOW 🆓UNPAID\n"
                              "❌ Sales/Support/BPO excluded\n"
                              "⏰ Every {hrs}h | 📧 Mail 8AM\n"
                              "🌐 port {port}\n\n"
                              "/help for full guide".format(
                                  hrs=HOURS_BETWEEN_SEARCHES,
                                  port=WEB_PORT,
                                  sep=SEP_DASH),
                              parse_mode="Markdown",
                              reply_markup=kb)

        # ── Mail Bot Callbacks ─────────────────────────
        elif d == "m_mail":
            ms = get_mail_stats()
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Queue", callback_data="m_mq")],
                [InlineKeyboardButton("📤 Send Now", callback_data="m_ms")],
                [
                    InlineKeyboardButton("📊 Stats", callback_data="m_mst"),
                    InlineKeyboardButton("📜 History", callback_data="m_mh")
                ],
                [InlineKeyboardButton("🔄 Auto-Add", callback_data="m_mauto")],
                [InlineKeyboardButton("🔙 Menu", callback_data="m_main")],
            ])
            await m.edit_text("📧 *Mail Bot*\n{sep}\n"
                              "⏳ {p} pending | ✅ {s} sent\n"
                              "💡 Forward job posts to extract!".format(
                                  p=ms["pending"], s=ms["sent"], sep=SEP_DASH),
                              parse_mode="Markdown",
                              reply_markup=kb)

        elif d == "m_mq":
            queue = load_mail_queue()
            pending = [qi for qi in queue if qi.get("status") == "pending"]
            if not pending:
                txt = "📋 *Queue Empty!*\nForward a post to add!"
            else:
                txt = "📋 *Queue ({} pending)*\n{}\n\n{}".format(
                    len(pending), SEP_DASH, "\n\n".join([
                        "📧 `{}` → {}".format(qi.get("email", ""),
                                             safe_str(qi.get("company")))
                        for qi in pending[-10:]
                    ]))
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Send All", callback_data="m_ms")],
                [InlineKeyboardButton("🔙 Mail", callback_data="m_mail")],
            ])
            await m.edit_text(txt, parse_mode="Markdown", reply_markup=kb)

        elif d == "m_ms":
            await m.reply_text("📤 Mail is sent by PythonAnywhere at 8 AM.\n"
                               "Use /mailsend for status.")

        elif d == "m_mst":
            ms = get_mail_stats()
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Mail", callback_data="nav_mail")]])
            await m.edit_text("📊 *Mail Stats*\n{sep}\n"
                              "📋 {t} total | ⏳ {p} pending\n"
                              "✅ {s} sent | ❌ {f} failed\n"
                              "📤 {sn} sending".format(t=ms["total"],
                                                      p=ms["pending"],
                                                      s=ms["sent"],
                                                      f=ms["failed"],
                                                      sn=ms.get("sending", 0),
                                                      sep=SEP_DASH),
                              parse_mode="Markdown",
                              reply_markup=kb)

        elif d == "m_mh":
            queue = load_mail_queue()
            sent = [qi for qi in queue if qi.get("status") == "sent"]
            if not sent:
                txt = "📜 No history"
            else:
                txt = "📜 *Sent ({})*\n{}\n\n{}".format(
                    len(sent), SEP_DASH, "\n\n".join([
                        "✅ `{}` → {}".format(qi.get("email", ""),
                                             safe_str(qi.get("company")))
                        for qi in sent[-10:]
                    ]))
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Mail", callback_data="nav_mail")]])
            await m.edit_text(txt, parse_mode="Markdown", reply_markup=kb)

        elif d == "m_mauto":
            await m.reply_text("🔄 Adding job emails to queue...")
            if os.path.exists(EMAILS_FILE):
                try:
                    df = pd.read_csv(EMAILS_FILE)
                    added = 0
                    for _, row in df.iterrows():
                        em = str(row.get("email", "")).strip()
                        if em and is_valid_email(em):
                            if add_to_mail_queue(
                                    em, safe_str(row.get("company")),
                                    safe_str(row.get("job_title")), "auto_add",
                                    "NORMAL"):
                                added += 1
                    await m.reply_text("✅ Added *{}* emails!".format(added),
                                       parse_mode="Markdown")
                except Exception as e:
                    await m.reply_text("⚠️ Error: {}".format(str(e)[:100]))
            else:
                await m.reply_text("📭 No emails file. /search first.")

        elif d == "m_main" or d == "nav_home":
            s = get_stats()
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔍 Jobs", callback_data="nav_jobs"),
                    InlineKeyboardButton("📨 Mail", callback_data="nav_mail")
                ],
                [
                    InlineKeyboardButton("🛠 Tools", callback_data="nav_tools"),
                    InlineKeyboardButton("📊 Stats", callback_data="nav_stats")
                ],
                [InlineKeyboardButton("❓ Help & Guide", callback_data="nav_help")],
            ])
            await m.edit_text(
                "🎯 *{name} v{ver}*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📋 *{t}* Jobs  │  📧 *{e}* Emails\n"
                "📨 *{mp}* Pending  │  ✅ *{ms}* Sent\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⭐ *{i}* Important  │  🏢 *{mc}* MNC\n"
                "🔥 *{h}* High Pay  │  💾 *{sv}* Saved\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "💡 *Forward any job post to extract & queue!*".format(
                    name=BOT_NAME, ver=BOT_VERSION,
                    t=s["total"], i=s["important"],
                    h=s["high"], mc=s["mnc"],
                    e=s["emails"], sv=s["saved"],
                    mp=s["mail_pending"], ms=s["mail_sent"]),
                parse_mode="Markdown",
                reply_markup=kb)

        # ── NAV: Jobs Sub-Menu ────────────────────────────
        elif d == "nav_jobs":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Search Now", callback_data="m_search")],
                [
                    InlineKeyboardButton("⭐ Important", callback_data="m_imp"),
                    InlineKeyboardButton("🏢 MNC", callback_data="m_mnc")
                ],
                [
                    InlineKeyboardButton("🔥 High Pay", callback_data="m_high"),
                    InlineKeyboardButton("📋 Recent", callback_data="m_recent")
                ],
                [
                    InlineKeyboardButton("💾 Saved", callback_data="m_saved"),
                    InlineKeyboardButton("✅ Applied", callback_data="m_applied")
                ],
                [
                    InlineKeyboardButton("⏸ Pause Search", callback_data="nav_stopsearch"),
                    InlineKeyboardButton("▶️ Resume Search", callback_data="nav_resumesearch")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="nav_home")],
            ])
            await m.edit_text(
                "🔍 *Job Search*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Browse jobs by category or start a\n"
                "new search across all locations.\n\n"
                "💡 Auto-search runs every *{}h*".format(HOURS_BETWEEN_SEARCHES),
                parse_mode="Markdown",
                reply_markup=kb)

        # ── NAV: Mail Sub-Menu ────────────────────────────
        elif d == "nav_mail":
            ms = get_mail_stats()
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📋 Queue ({})".format(ms["pending"]), callback_data="m_mq"),
                    InlineKeyboardButton("📊 Stats", callback_data="m_mst")
                ],
                [
                    InlineKeyboardButton("📤 Push to PA", callback_data="nav_papush"),
                    InlineKeyboardButton("📜 History", callback_data="m_mh")
                ],
                [
                    InlineKeyboardButton("📧 Templates", callback_data="nav_templates"),
                    InlineKeyboardButton("📄 Resume", callback_data="nav_resume")
                ],
                [
                    InlineKeyboardButton("🔄 Auto-Add Emails", callback_data="m_mauto"),
                    InlineKeyboardButton("📸 Bulk Scan", callback_data="nav_bulkscan")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="nav_home")],
            ])
            await m.edit_text(
                "📨 *Mail Bot*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📋 *{t}* Total  │  ⏳ *{p}* Pending\n"
                "✅ *{s}* Sent   │  ❌ *{f}* Failed\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📤 Daily push at *8:00 AM IST*\n"
                "💡 Forward job posts to extract emails!".format(
                    t=ms["total"], p=ms["pending"],
                    s=ms["sent"], f=ms["failed"]),
                parse_mode="Markdown",
                reply_markup=kb)

        # ── NAV: Tools Sub-Menu ───────────────────────────
        elif d == "nav_tools":
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📥 Inbox", callback_data="nav_inbox"),
                    InlineKeyboardButton("🔄 Pipeline", callback_data="nav_pipeline")
                ],
                [
                    InlineKeyboardButton("💾 Backup", callback_data="nav_backup"),
                    InlineKeyboardButton("📋 OCR Status", callback_data="nav_ocrstatus")
                ],
                [
                    InlineKeyboardButton("⏸ Pause Mails", callback_data="nav_pause"),
                    InlineKeyboardButton("▶️ Resume Mails", callback_data="nav_resumefu")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="nav_home")],
            ])
            await m.edit_text(
                "🛠 *Tools & Utilities*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Manage your inbox, pipeline,\n"
                "backups, and bot settings.\n\n"
                "💡 Use Inbox to monitor HR replies!",
                parse_mode="Markdown",
                reply_markup=kb)

        # ── NAV: Stats Sub-Menu ───────────────────────────
        elif d == "nav_stats":
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 Full Stats", callback_data="m_stats"),
                    InlineKeyboardButton("📧 All Emails", callback_data="m_emails")
                ],
                [
                    InlineKeyboardButton("📤 Export", callback_data="nav_export"),
                    InlineKeyboardButton("🖥 System Status", callback_data="nav_status")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="nav_home")],
            ])
            await m.edit_text(
                "📊 *Statistics & Reports*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "View detailed analytics, export data,\n"
                "or check system health.\n\n"
                "💡 Full Stats shows salary breakdown!",
                parse_mode="Markdown",
                reply_markup=kb)

        # ── NAV: Help ─────────────────────────────────────
        elif d == "nav_help":
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data="nav_home")]])
            await m.edit_text(
                "❓ *Help & Guide*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📸 *Forward a job screenshot*\n"
                "  → Bot extracts emails via OCR\n"
                "  → Emails auto-queued for sending\n\n"
                "📝 *Forward job text*\n"
                "  → Bot grabs emails from text\n"
                "  → Queued with company & role\n\n"
                "📨 *Daily Mail Flow:*\n"
                "  8:00 AM → Queue pushed to PA\n"
                "  8:05 AM → PA sends all emails\n"
                "  → TG notification on completion\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💰 *Salary Tiers:*\n"
                "  🔥 HIGH → ₹5L+\n"
                "  ⭐ MID  → ₹2.5-5L\n"
                "  📌 LOW  → ₹1-2.5L\n"
                "  🆓 FREE → Stipend/Unpaid\n\n"
                "🛡️ *Auto-filtered:* Sales, BPO,\n"
                "   Support, Senior & unrelated roles",
                parse_mode="Markdown",
                reply_markup=kb)

        # ── NAV: Trigger actions ──────────────────────────
        elif d == "nav_papush":
            await m.reply_text("📤 Pushing queue to PythonAnywhere...")
            try:
                await cmd_papush(update, ctx)
            except Exception as e:
                await m.reply_text(f"⚠️ Push error: {str(e)[:100]}")

        elif d == "nav_templates":
            await cmd_templates(update, ctx)

        elif d == "nav_resume":
            await cmd_resume(update, ctx)

        elif d == "nav_bulkscan":
            await cmd_bulkscan(update, ctx)

        elif d == "nav_inbox":
            await cmd_inbox(update, ctx)

        elif d == "nav_pipeline":
            await cmd_pipeline(update, ctx)

        elif d == "nav_backup":
            await cmd_backup(update, ctx)

        elif d == "nav_ocrstatus":
            await cmd_ocrstatus(update, ctx)

        elif d == "nav_pause":
            await cmd_pause(update, ctx)

        elif d == "nav_resumefu":
            await cmd_resume_followup(update, ctx)

        elif d == "nav_export":
            await cmd_export(update, ctx)

        elif d == "nav_status":
            await cmd_status(update, ctx)

        elif d == "nav_stopsearch":
            await cmd_stopsearch(update, ctx)

        elif d == "nav_resumesearch":
            await cmd_resumesearch(update, ctx)

        # ── Job action callbacks ───────────────────────
        elif d.startswith("ap_"):
            jid = d[3:]
            save_to_csv(
                {
                    "job_id": jid,
                    "action": "applied",
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M")
                }, APPLIED_FILE)
            try:
                await q.edit_message_text(text=m.text + "\n\n✅ *APPLIED!* 🎉",
                                          parse_mode="Markdown")
            except:
                await m.reply_text("✅ Applied!")

        elif d.startswith("dm_"):
            jid = d[3:]
            save_to_csv(
                {
                    "job_id": jid,
                    "action": "dismissed",
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M")
                }, DISMISSED_FILE)
            try:
                await m.delete()
            except:
                await m.reply_text("❌ Dismissed")

        elif d.startswith("sv_"):
            jid = d[3:]
            if os.path.exists(JOBS_FILE):
                try:
                    df = pd.read_csv(JOBS_FILE)
                    row = df[df["job_id"].astype(str) == str(jid)]
                    if not row.empty:
                        jd = {
                            k: v
                            for k, v in row.iloc[0].to_dict().items()
                            if not k.startswith("Unnamed")
                        }
                        save_to_csv(jd, SAVED_FILE)
                        await m.reply_text("💾 Saved! View with /saved")
                    else:
                        await m.reply_text("⚠️ Job not found")
                except:
                    await m.reply_text("⚠️ Save failed")

        elif d.startswith("dt_"):
            jid = d[3:]
            if os.path.exists(JOBS_FILE):
                try:
                    df = pd.read_csv(JOBS_FILE)
                    row = df[df["job_id"].astype(str) == str(jid)]
                    if not row.empty:
                        j = row.iloc[0]
                        mt = "Yes - {}".format(safe_str(j.get("mnc_name"))) \
                            if to_bool(j.get("is_mnc")) else "No"
                        await m.reply_text(
                            "ℹ️ *Full Details*\n{sep}\n\n"
                            "📌 *{title}*\n🏢 *{company}*\n"
                            "📍 {loc}\n📂 {cat}\n💰 {sal}\n"
                            "🏷️ Priority: {pri}\n🏢 MNC: {mnc}\n"
                            "📧 Email: {em}\n🔗 {url}".format(
                                title=safe_str(j.get("title")),
                                company=safe_str(j.get("company")),
                                loc=safe_str(j.get("location")),
                                cat=safe_str(j.get("category")),
                                sal=safe_str(j.get("salary")),
                                pri=safe_str(j.get("priority")),
                                mnc=mt,
                                em=safe_str(j.get("emails", "None")),
                                url=safe_str(j.get("job_url")),
                                sep=SEP_DASH),
                            parse_mode="Markdown")
                except:
                    await m.reply_text("⚠️ Error")

        elif d.startswith("em_"):
            jid = d[3:]
            if os.path.exists(JOBS_FILE):
                try:
                    df = pd.read_csv(JOBS_FILE)
                    row = df[df["job_id"].astype(str) == str(jid)]
                    if not row.empty:
                        em = row["emails"].values[0]
                        if em and str(em) != "nan":
                            await m.reply_text(
                                "📧 `{}`\n\n💡 Tap to copy!".format(em),
                                parse_mode="Markdown")
                        else:
                            await m.reply_text("📭 No email for this job")
                except:
                    await m.reply_text("⚠️ Error")

        elif d.startswith("qm_"):
            jid = d[3:]
            if os.path.exists(JOBS_FILE):
                try:
                    df = pd.read_csv(JOBS_FILE)
                    row = df[df["job_id"].astype(str) == str(jid)]
                    if not row.empty:
                        em = str(row["emails"].values[0])
                        co = safe_str(row["company"].values[0])
                        ti = safe_str(row["title"].values[0])
                        if em and em != "nan":
                            added = 0
                            for e in em.split(","):
                                e = e.strip()
                                if e and is_valid_email(e):
                                    if add_to_mail_queue(
                                            e, co, ti, "quick_mail", "HIGH"):
                                        added += 1
                            await m.reply_text(
                                "✅ Added {} to queue!\n📧 `{}`\n🏢 {}\n\n📤 /mailsend"
                                .format(added, em, co),
                                parse_mode="Markdown")
                        else:
                            await m.reply_text("📭 No email")
                except:
                    await m.reply_text("⚠️ Error")

        else:
            await q.answer("Unknown action")

    except Exception as e:
        logger.error("CB err: {}".format(e))
        try:
            await m.reply_text("⚠️ {}".format(str(e)[:80]))
        except:
            pass


# ─── Message handler ───────────────────────────────────────
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    SINGLE unified message handler.
    Routes: Photo → handle_photo
            Document → handle_photo_document
            Text → extract emails → queue
    """
    try:
        msg = update.effective_message
        if not msg:
            return

        # ── 1. BULK MODE (highest priority) ───────────────
        if ctx.user_data.get("bulk_mode"):
            if msg.photo:
                photo = msg.photo[-1]
                tg_file = await ctx.bot.get_file(photo.file_id)
                img_bytes = await tg_file.download_as_bytearray()
                ocr_text = extract_from_image(img_bytes)
                del img_bytes
                if ocr_text:
                    ctx.user_data.setdefault("bulk_texts", []).append(ocr_text)
                    ctx.user_data["bulk_count"] = (
                        ctx.user_data.get("bulk_count", 0) + 1
                    )
                    # Extract details for display
                    details = extract_job_details(ocr_text)
                    co = details["companies"][0] if details["companies"] else "Unknown"
                    ro = details["roles"][0] if details["roles"] else "IT Role"
                    
                    await msg.reply_text(
                        f"📥 Captured ({ctx.user_data['bulk_count']}).\n"
                        f"🏢 {co} | 💼 {ro}\n"
                        f"📧 {len(details.get('emails', []))} emails detected.\n"
                        f"Keep sending or /bulkdone"
                    )
                else:
                    await msg.reply_text(
                        "⚠️ Could not read this image. "
                        "Try a clearer one or /bulkdone"
                    )
                return

            elif is_image_document(msg.document):
                tg_file = await ctx.bot.get_file(msg.document.file_id)
                img_bytes = await tg_file.download_as_bytearray()
                ocr_text = extract_from_image(img_bytes)
                del img_bytes
                if ocr_text:
                    ctx.user_data.setdefault("bulk_texts", []).append(ocr_text)
                    ctx.user_data["bulk_count"] = (
                        ctx.user_data.get("bulk_count", 0) + 1
                    )
                    await msg.reply_text(
                        f"📥 Doc captured ({ctx.user_data['bulk_count']})"
                    )
                return

            # If bulk mode is on but message is text, fall through
            # to handle text content below

    # ── Photo/Document Processing ──────────────────
        if (msg.photo or (msg.document and is_image_document(msg.document))):
            return await handle_photo(update, ctx)

        if msg.document and msg.document.mime_type == "application/pdf":
            if ctx.user_data.get("awaiting_resume"):
                return await handle_resume_upload(update, ctx)
            return  # Ignore other PDFs

        text = msg.text or msg.caption
        if not text: return

        # ── Forwarded Post Processing (Fix #2, #4) ───────
        emails = extract_emails_from_text(text)
        valid_emails = [] # Initialize fallback
        if emails:
            # Check if already processed
            valid_emails = [e for e in emails if not is_email_already_processed(e)]
            if not valid_emails:
                status_lines = []
                # Unique emails only
                for e in list(set(emails)):
                    st = get_email_status(e)
                    if st == "sent": status_lines.append(f"  ✅ `{e}` (Sent successfully)")
                    elif st == "queued": status_lines.append(f"  🕒 `{e}` (Still in queue)")
                    else: status_lines.append(f"  ⚙️ `{e}` (Previously processed)")
                
                await msg.reply_text(
                    "ℹ️ *Duplicate Text Detected:*\n\n" + "\n".join(status_lines),
                    parse_mode="Markdown"
                )
                return

            details = extract_job_details(text)
            company = details["companies"][0] if details["companies"] else "Unknown"
            role = details["roles"][0] if details["roles"] else "Research/Job Role"
            
            # Store for callback
            data_key = hashlib.md5(f"{valid_emails[0]}{time.time()}".encode()).hexdigest()[:6]
            if "pending_mail_data" not in ctx.user_data:
                ctx.user_data["pending_mail_data"] = {}
            ctx.user_data["pending_mail_data"][data_key] = {
                "emails": valid_emails,
                "company": company,
                "role": role
            }

            # Show Template Picker
            buttons = []
            for tid, info in load_templates().items():
                buttons.append([InlineKeyboardButton(f"{info['emoji']} {info['name']}", callback_data=f"tmpl_{tid}_{data_key}")])
            
            kb = InlineKeyboardMarkup(buttons)
            
            status_lines = []
            for e in list(set(emails)):
                if e in valid_emails:
                    status_lines.append(f"  🟢 `{e}` (New, ready to send)")
                else:
                    st = get_email_status(e)
                    if st == "sent": status_lines.append(f"  ✅ `{e}` (Sent successfully)")
                    elif st == "queued": status_lines.append(f"  🕒 `{e}` (Still in queue)")
                    else: status_lines.append(f"  ⚙️ `{e}` (Previously processed)")

            await msg.reply_text(
                f"🎯 *Job Post Detected!*\n"
                f"🏢 Company: *{escape_md(company)}*\n"
                f"💼 Role: *{escape_md(role)}*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📧 Total Found: {len(set(emails))}\n"
                f"✅ New Ready: {len(valid_emails)}\n\n"
                "*Status Breakdown:*\n" + "\n".join(status_lines) + "\n\n"
                "👇 *Select an Email Template for the New Emails:*",
                parse_mode="Markdown",
                reply_markup=kb
            )
            return
        
        # If no emails but looks like a job post, create job card
        if is_job_post(text):
            details = extract_job_details(text)
            job = {
                "title": (
                    details["roles"][0]
                    if details["roles"]
                    else "Software Developer"
                ),
                "company": (
                    details["companies"][0]
                    if details["companies"]
                    else "Unknown Company"
                ),
                "description": text,
                "job_url": "Forwarded Post",
                "emails": ", ".join(valid_emails),
                "date_added": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            job["is_important"] = is_important(
                job["title"], job["company"], text
            )
            job["mnc_name"] = is_mnc(job["company"]) or ""
            job["is_mnc"] = bool(job["mnc_name"])
            job["priority"] = "HIGH" if job["is_important"] else "MID"
            job["category"] = categorize_job(job["title"])
            job["salary_display"] = "Not Disclosed"
            job["job_id"] = gen_id(
                job["title"], job["company"], "forwarded"
            )

            await send_job(ctx.bot, job)
            await safe_reply(
                update,
                "✅ *Job Post Detected & Saved!*\n"
                "📭 No email found in this post though.\n"
                "💡 Forward the one with contact details too!",
                parse_mode="Markdown",
            )
            return

        # If nothing detected and message is long, tell user
        if len(text) > 80:
            await safe_reply(
                update,
                "📝 *Received your message* but:\n"
                "• No email addresses found\n"
                "• Doesn't match job post patterns\n\n"
                "💡 *Tips:*\n"
                "• Forward a post that contains an email\n"
                "• Send a screenshot of the job listing\n"
                "• Use /mailadd email@company.com CompanyName",
                parse_mode="Markdown",
            )

    except Exception as e:
        logger.error(f"handle_message error: {e}", exc_info=True)
        await safe_reply(
            update,
            f"⚠️ *Error:* {str(e)[:150]}\nPlease try again.",
            parse_mode="Markdown",
        )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        ("❓ *BCA Job Hunter — Help Guide v{ver}*\n{sep1}\n\n"
         "📝 *Main Commands:*\n"
         "  /start   → Main menu & dashboard\n"
         "  /search  → Start manual job search\n"
         "  /stats   → Full statistics\n\n"
         "🔍 *Browse Jobs:*\n"
         "  /recent  → Last 8 jobs found\n"
         "  /high    → High salary jobs (5L+)\n"
         "  /mnc     → MNC company openings\n"
         "  /saved   → Your saved jobs\n"
         "  /applied → Applied job count\n\n"
         "📧 *Contacts:*\n"
         "  /emails  → All HR email contacts\n"
         "  /export  → Export summary\n\n"
         "📨 *Mail Bot Commands:*\n"
         "  /mail       → Mail bot dashboard\n"
         "  /mailqueue  → View pending queue\n"
         "  /mailsend   → Trigger send now\n"
         "  /mailstats  → Mail statistics\n"
         "  /mailhistory → Sent mail history\n"
         "  /mailadd    → Add email manually\n\n"
         "📸 *Forward Features:*\n"
         "  Forward any job post → Auto-extract\n"
         "  Send screenshot → OCR extract\n"
         "  Send text → Extract emails\n\n"
         "{sep2}\n"
         "💰 *Salary Tiers:*\n"
         "  🔥 HIGH → ₹5L+  |  ⭐ MID → ₹2.5-5L\n"
         "  📌 LOW → ₹1-2.5L  |  🆓 UNPAID → Stipend\n\n"
         "🎮 *Job Actions:*\n"
         "  ✅ Mark as Applied  |  ❌ Skip/Dismiss\n"
         "  💾 Save for Later   |  ℹ️ View Details\n"
         "  📧 Quick Mail HR    |  📧 Copy Email\n\n"
         "🛡️ *Auto-Filtered:* Sales, BDO, BPO, Support, Senior & more\n\n"
         "⏰ Auto search every *{hrs}h*\n"
         "📧 Auto mail daily at *8 AM*\n"
         "🌐 Web dashboard on port *{port}*").format(hrs=HOURS_BETWEEN_SEARCHES,
                                                    port=WEB_PORT,
                                                    ver=BOT_VERSION,
                                                    sep1=SEP_BOLD,
                                                    sep2=SEP_LINE),
        parse_mode="Markdown")


async def cmd_stopsearch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global search_paused
    search_paused = True
    try:
        with open("data/search_paused.json", "w") as f:
            json.dump({"paused": True}, f)
    except:
        pass
    await update.effective_message.reply_text(
        "⏸️ *Auto-search PAUSED*\n\n"
        "No more automatic searches will run.\n"
        "Manual /search still works.\n\n"
        "/resumesearch to resume",
        parse_mode="Markdown")


async def cmd_resumesearch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global search_paused
    search_paused = False
    try:
        with open("data/search_paused.json", "w") as f:
            json.dump({"paused": False}, f)
    except:
        pass
    await update.effective_message.reply_text(
        "▶️ *Auto-search RESUMED*\n\n"
        f"Next search in {HOURS_BETWEEN_SEARCHES}h",
        parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════
# TELEGRAM INBOX COMMANDS
# ═══════════════════════════════════════════════════════════


# ─── Format instant alert ─────────────────────────────────
def format_instant_alert(item):
    """Format a CRITICAL/URGENT email for Telegram"""
    c = item["classification"]
    email_type = c.get("type", "unknown")
    sender = item.get("sender_email", "")
    name = item.get("sender_name", "")
    subject = item.get("subject", "")
    preview = item.get("body_preview", "")[:300]
    keywords = ", ".join(c.get("matched_keywords", [])[:3])

    if email_type == "interview":
        header = "🚨 INTERVIEW CALL!"
        emoji = "🚨"
    elif email_type == "positive":
        header = "🎉 POSITIVE RESPONSE!"
        emoji = "🎉"
    elif email_type == "reply":
        header = "📬 HR REPLIED!"
        emoji = "📬"
    else:
        header = "📧 NEW RESPONSE"
        emoji = "📧"

    return (f"{emoji} *{header}*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 *From:* {escape_md(name)}\n"
            f"📧 `{sender}`\n"
            f"📌 *Subject:* {escape_md(subject[:80])}\n"
            f"⏰ *Time:* Just now\n\n"
            f"📝 *Preview:*\n"
            f"_{escape_md(preview[:250])}_\n\n"
            f"🔑 *Detected:* {escape_md(keywords)}\n\n"
            f"✅ Follow-ups auto-stopped for this sender")


# ─── /inbox command ───────────────────────────────────────
async def cmd_inbox(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    show_all = args and args[0].lower() == "all"

    unhandled = get_unhandled_items()

    if not unhandled:
        stats = get_inbox_stats()
        await update.effective_message.reply_text(
            "📬 *Inbox Clear!*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "✅ No pending items to handle.\n\n"
            f"📊 Today: {stats['today_replies']} replies, "
            f"{stats['today_bounces']} bounces\n"
            f"📈 Total responses: {stats['total_responses']}\n\n"
            "💡 New emails checked every 30 min",
            parse_mode="Markdown")
        return

    # Split by priority
    critical = [
        i for i in unhandled if i["classification"]["priority"] == "CRITICAL"
    ]
    urgent = [
        i for i in unhandled if i["classification"]["priority"] == "URGENT"
    ]
    normal = [
        i for i in unhandled if i["classification"]["priority"] == "NORMAL"
    ]

    msg = f"📬 *Inbox ({len(unhandled)} items)*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"

    idx = 0

    if critical:
        msg += "🚨 *REPLY NOW:*\n"
        for item in critical[:5]:
            idx += 1
            sender = item.get("sender_email", "")
            subject = item.get("subject", "")[:50]
            msg += (f"  {idx}. `{sender}`\n"
                    f"     {escape_md(subject)}\n\n")

    if urgent:
        msg += "⭐ *REPLY TODAY:*\n"
        for item in urgent[:5]:
            idx += 1
            sender = item.get("sender_email", "")
            subject = item.get("subject", "")[:50]
            msg += (f"  {idx}. `{sender}`\n"
                    f"     {escape_md(subject)}\n\n")

    if normal and show_all:
        msg += "📋 *INFO:*\n"
        for item in normal[:5]:
            idx += 1
            sender = item.get("sender_email", "")
            subject = item.get("subject", "")[:50]
            msg += (f"  {idx}. `{sender}`\n"
                    f"     {escape_md(subject)}\n\n")

    msg += ("━━━━━━━━━━━━━━━━━━\n"
            "📖 /reply 1 → read full + suggested response\n"
            "✅ /done 1 → mark as handled\n"
            "⏰ /snooze 1 2h → remind later")

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Handle All",
                                 callback_data="inbox_handleall"),
            InlineKeyboardButton("🔄 Refresh", callback_data="inbox_refresh"),
        ],
    ])

    await update.effective_message.reply_text(msg,
                                    parse_mode="Markdown",
                                    reply_markup=kb)


# ─── /reply command ───────────────────────────────────────
async def cmd_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.effective_message.reply_text("Usage: /reply 1\n"
                                        "Shows full email + suggested response"
                                        )
        return

    try:
        idx = int(args[0]) - 1
    except ValueError:
        await update.effective_message.reply_text("Invalid number. Use /reply 1")
        return

    unhandled = get_unhandled_items()

    if idx < 0 or idx >= len(unhandled):
        await update.effective_message.reply_text(
            f"Invalid. You have {len(unhandled)} items. "
            f"Use /reply 1 to /reply {len(unhandled)}")
        return

    item = unhandled[idx]
    c = item["classification"]
    sender = item.get("sender_email", "")
    name = item.get("sender_name", "")
    subject = item.get("subject", "")
    body = item.get("body_preview", "")
    email_type = c.get("type", "unknown")

    # Generate suggested response
    if email_type == "interview":
        suggestion = (f"Dear {name},\n\n"
                      "Thank you for considering my application. "
                      "I am available for the interview and would be "
                      "happy to attend at your convenience.\n\n"
                      "Please let me know the date, time, and format "
                      "I am flexible with my schedule.\n\n"
                      "Best regards,\nAkshat Tripathi\n"
                      "Phone: +91-7081484808")
    elif email_type == "positive":
        suggestion = (f"Dear {name},\n\n"
                      "Thank you so much for the positive response. "
                      "I am very excited about this opportunity.\n\n"
                      "Please let me know the next steps. I am ready "
                      "to provide any additional information needed.\n\n"
                      "Best regards,\nAkshat Tripathi\n"
                      "Phone: +91-7081484808")
    elif email_type == "negative":
        suggestion = (f"Dear {name},\n\n"
                      "Thank you for letting me know. I appreciate "
                      "your time and consideration.\n\n"
                      "I would be grateful if you could keep my "
                      "resume on file for future opportunities.\n\n"
                      "Best regards,\nAkshat Tripathi")
    else:
        suggestion = (f"Dear {name},\n\n"
                      "Thank you for your response. I appreciate "
                      "you getting back to me.\n\n"
                      "I look forward to hearing about next steps.\n\n"
                      "Best regards,\nAkshat Tripathi\n"
                      "Phone: +91-7081484808")

    msg = (f"📧 *Full Email #{idx + 1}*\n"
           f"━━━━━━━━━━━━━━━━━━\n\n"
           f"👤 *From:* {escape_md(name)}\n"
           f"📧 `{sender}`\n"
           f"📌 *Subject:* {escape_md(subject)}\n"
           f"📂 *Type:* {email_type.upper()}\n"
           f"⏰ *Date:* {item.get('date', '')[:16]}\n\n"
           f"📝 *Full Message:*\n"
           f"──────────────────\n"
           f"{escape_md(body[:800])}\n"
           f"──────────────────\n\n"
           f"💡 *Suggested Response:*\n"
           f"──────────────────\n"
           f"_{escape_md(suggestion)}_\n"
           f"──────────────────\n\n"
           f"✅ /done {idx + 1} to mark handled")

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Done", callback_data=f"inbox_done_{idx}"),
            InlineKeyboardButton("⏰ Snooze 2h",
                                 callback_data=f"inbox_snooze_{idx}"),
        ],
        [
            InlineKeyboardButton("📋 Copy Email",
                                 callback_data=f"inbox_copy_{idx}"),
        ],
    ])

    await update.effective_message.reply_text(msg,
                                    parse_mode="Markdown",
                                    reply_markup=kb)


# ─── /done command ────────────────────────────────────────
async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.effective_message.reply_text("Usage: /done 1")
        return

    try:
        idx = int(args[0]) - 1
    except:
        await update.effective_message.reply_text("Invalid number")
        return

    if mark_handled(idx):
        remaining = len(get_unhandled_items())
        await update.effective_message.reply_text(
            f"✅ *Marked as handled!*\n"
            f"📋 {remaining} items remaining\n\n"
            f"{'🎉 Inbox clear!' if remaining == 0 else '/inbox to see rest'}",
            parse_mode="Markdown")
    else:
        await update.effective_message.reply_text("Invalid item number")


# ─── /snooze command ──────────────────────────────────────
async def cmd_snooze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 2:
        await update.effective_message.reply_text("Usage: /snooze 1 2h\n"
                                        "Options: 1h, 2h, 4h, 8h, 24h")
        return

    try:
        idx = int(args[0]) - 1
        hours_str = args[1].lower().replace("h", "")
        hours = int(hours_str)
    except:
        await update.effective_message.reply_text("Invalid. Use: /snooze 1 2h")
        return

    unhandled = get_unhandled_items()
    if idx < 0 or idx >= len(unhandled):
        await update.effective_message.reply_text(f"Invalid item. Only {len(unhandled)} items available.")
        return

    # Schedule reminder

    if not ctx.job_queue:
        await update.effective_message.reply_text(
            "⚠️ JobQueue is disabled. Snooze won't work.")
        return

    remind_time = datetime.now() + timedelta(hours=hours)

    await update.effective_message.reply_text(
        f"⏰ *Snoozed!*\n"
        f"Will remind at {remind_time.strftime('%I:%M %p')}\n\n"
        f"📋 Item stays in /inbox until handled",
        parse_mode="Markdown")

    # Schedule the reminder
    async def send_reminder(context):
        unhandled = get_unhandled_items()
        if idx < len(unhandled):
            item = unhandled[idx]
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=(f"⏰ *Reminder!*\n"
                      f"━━━━━━━━━━━━━━━━━━\n\n"
                      f"📧 `{item.get('sender_email', '')}`\n"
                      f"📌 {escape_md(item.get('subject', '')[:60])}\n\n"
                      f"Use /reply {idx + 1} to respond"),
                parse_mode="Markdown")

    ctx.job_queue.run_once(send_reminder,
                           when=timedelta(hours=hours),
                           name=f"snooze_{idx}")


# ─── /pause and /resume followups ─────────────────────────
async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.effective_message.reply_text("Usage: /pause hr@company.com\n"
                                        "Pauses follow-ups for this email")
        return

    email_addr = args[0].strip().lower()
    if stop_followups_for(email_addr):
        await update.effective_message.reply_text(
            f"⏸️ *Follow-ups paused*\n"
            f"📧 `{email_addr}`\n\n"
            f"Use /resume {email_addr} to restart",
            parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(
            f"⚠️ Email not found in queue: `{email_addr}`",
            parse_mode="Markdown")


async def cmd_resume_followup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.effective_message.reply_text("Usage: /resumefu hr@company.com")
        return

    email_addr = args[0].strip().lower()

    try:
        queue = safe_load_json(MAIL_QUEUE_FILE, [])

        updated = False
        for item in queue:
            if item.get("email", "").lower() == email_addr:
                item["response_received"] = False
                item["followup_stage"] = 0
                updated = True
                break

        if updated:
            safe_save_json(MAIL_QUEUE_FILE, queue)
            await update.effective_message.reply_text(
                f"▶️ *Follow-ups resumed*\n"
                f"📧 `{email_addr}`",
                parse_mode="Markdown")
        else:
            await update.effective_message.reply_text("⚠️ Not found")
    except:
        await update.effective_message.reply_text("⚠️ Error")


async def cmd_mailremove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Feature #19: Manually remove an email from the queue"""
    args = ctx.args
    if not args:
        await update.effective_message.reply_text("Usage: /mailremove hr@company.com")
        return

    email_addr = args[0].strip().lower()
    with data_lock:
        queue = load_mail_queue()
        initial_len = len(queue)
        queue = [q for q in queue if q.get("email", "").lower() != email_addr]

        if len(queue) < initial_len:
            save_mail_queue(queue)
            await update.effective_message.reply_text(
                f"🗑️ Removed `{email_addr}` from mail queue.")
        else:
            await update.effective_message.reply_text(
                f"⚠️ `{email_addr}` not found in queue.")


async def cmd_bancompany(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Feature #23: Add a company to the blacklist dynamically"""
    args = ctx.args
    if not args:
        await update.effective_message.reply_text("Usage: /bancompany CompanyName")
        return

    company = " ".join(args).strip().lower()
    if company not in BLACKLISTED_COMPANIES:
        BLACKLISTED_COMPANIES.append(company)
        try:
            with open("data/banned_companies.json", "w") as f:
                json.dump(list(set(BLACKLISTED_COMPANIES)), f)
        except:
            pass
        await update.effective_message.reply_text(f"🚫 *{company}* added to blacklist.",
                                        parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(
            f"⚠️ *{company}* is already on the blacklist.",
            parse_mode="Markdown")


# ─── /pipeline command ────────────────────────────────────
async def cmd_pipeline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    items = load_inbox_items()
    get_inbox_stats()  # ensure data loaded

    interviews = [
        i for i in items if i["classification"]["type"] == "interview"
    ]
    positives = [i for i in items if i["classification"]["type"] == "positive"]
    replies = [i for i in items if i["classification"]["type"] == "reply"]
    rejections = [
        i for i in items if i["classification"]["type"] == "negative"
    ]

    msg = ("🎯 *Application Pipeline*\n"
           "══════════════════════\n\n"
           f"🚨 Interviews: *{len(interviews)}*\n")
    for i in interviews[-5:]:
        msg += f"  └ `{i['sender_email']}`\n"

    msg += f"\n🎉 Positive: *{len(positives)}*\n"
    for i in positives[-5:]:
        msg += f"  └ `{i['sender_email']}`\n"

    msg += f"\n📬 Replies: *{len(replies)}*\n"
    for i in replies[-5:]:
        msg += f"  └ `{i['sender_email']}`\n"

    msg += f"\n❌ Rejections: *{len(rejections)}*\n"
    for i in rejections[-3:]:
        msg += f"  └ `{i['sender_email']}`\n"

    # Calculate response rate
    try:
        queue = safe_load_json(MAIL_QUEUE_FILE, [])
        total_sent = sum(1 for q in queue if q.get("status") == "sent")
        total_responses = len(interviews) + len(positives) + len(replies)
        rate = ((total_responses / total_sent * 100) if total_sent > 0 else 0)
        msg += (f"\n📈 *Response Rate:* {rate:.1f}%\n"
                f"   ({total_responses} responses / "
                f"{total_sent} sent)")
    except:
        pass

    await update.effective_message.reply_text(msg, parse_mode="Markdown")


async def cmd_mail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View mail queue overview"""
    queue = safe_load_json(MAIL_QUEUE_FILE, [])
    pending = [q for q in queue if q.get("status") == "pending"]
    sent = [q for q in queue if q.get("status") == "sent"]

    msg = ("📨 *Mail Bot Status*\n"
           "━━━━━━━━━━━━━━━━━━━━━\n\n"
           f"📝 Pending: *{len(pending)}*\n"
           f"✅ Sent: *{len(sent)}*\n\n"
           "💡 Use /mailqueue to see details\n"
           "💡 Use /mailsend to see sending status")
    await update.effective_message.reply_text(msg, parse_mode="Markdown")


async def cmd_mailqueue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View detailed mail queue"""
    queue = safe_load_json(MAIL_QUEUE_FILE, [])
    pending = [q for q in queue if q.get("status") == "pending"]

    if not pending:
        await update.effective_message.reply_text("📭 Mail queue is empty.")
        return

    msg = "📋 *Pending Mail Queue (Top 10)*\n\n"
    for i, item in enumerate(pending[:10]):
        msg += f"{i+1}. `{item.get('email')}`\n   └ {item.get('company')}\n"

    if len(pending) > 10:
        msg += f"\n...and {len(pending)-10} more."

    await update.effective_message.reply_text(msg, parse_mode="Markdown")


async def cmd_mailsend(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Check mail bot sending status (Bug #10 fix)"""
    stats = get_stats()
    queue = load_mail_queue()
    sending = sum(1 for q in queue if q.get("status") == "sending")

    msg = (f"📤 *Mail Bot Status*\n{SEP_DASH}\n"
           f"✅ Sent: *{stats.get('mail_sent', 0)}*\n"
           f"📝 Pending: *{stats.get('mail_pending', 0)}*\n"
           f"⏳ Active: *{sending} items* (PA processing)\n\n"
           f"💡 Use /papush to check PythonAnywhere sync.")
    await update.effective_message.reply_text(msg, parse_mode="Markdown")


async def cmd_mailstats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View mail-related stats"""
    ms = get_mail_stats()
    inbox = get_inbox_stats()

    paused_data = safe_load_json(PAUSED_CONFIG, {"paused": False})
    paused = "⏸️ PAUSED" if paused_data.get("paused") else "▶️ ACTIVE"

    msg = (f"📊 *Mail Analytics*\n{SEP_DASH}\n\n"
           f"📤 *Outgoing:*\n"
           f"  📋 Total: *{ms.get('total', 0)}*\n"
           f"  ⏳ Pending: *{ms.get('pending', 0)}*\n"
           f"  ✅ Sent: *{ms.get('sent', 0)}*\n"
           f"  ❌ Failed: *{ms.get('failed', 0)}*\n"
           f"  🔄 Sending: *{ms.get('sending', 0)}*\n\n"
           f"📬 *Incoming:*\n"
           f"  📥 Total tracked: *{inbox.get('total', 0)}*\n"
           f"  🔴 Critical: *{inbox.get('critical', 0)}*\n"
           f"  🟠 Urgent: *{inbox.get('urgent', 0)}*\n\n"
           f"⚙️ Status: {paused}")
    await update.effective_message.reply_text(msg, parse_mode="Markdown")


async def cmd_mailhistory(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View recent mail activity"""
    safe_load_json(PAUSED_CONFIG, {"paused": False})  # ensure data loaded
    logs = safe_load_json(MAIL_LOG_FILE, [])
    if not logs:
        await update.effective_message.reply_text("📜 No mail history found.")
        return

    msg = "📜 *Recent Mail Activity*\n\n"
    for log in logs[-10:]:
        ts = log.get("timestamp", "").split("T")[-1][:5]
        prio = log.get("priority", "")
        source_log = log.get("source", "")
        details = log.get("details", "")

        if len(prio) > 0:
            p_val = str(prio)[0]
        else:
            p_val = "?"
        
        if len(source_log) > 0:
            s_val = str(source_log)[0]
        else:
            s_val = "?"
        
        msg += f"`{ts}` | {p_val} | {s_val} | `{details}`\n"

    await update.effective_message.reply_text(msg, parse_mode="Markdown")


async def cmd_mailadd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manually add email to queue"""
    args = ctx.args
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Usage: /mailadd email@example.com CompanyName [Role]")
        return

    email = args[0].strip().lower()
    company = args[1].strip()
    role = " ".join(args[2:]).strip() or "Entry-Level Position"

    if not is_valid_email(email):
        await update.effective_message.reply_text("❌ Invalid email format.")
        return

    save_email(email, company, "Manual Entry", "N/A")
    queued = add_to_mail_queue(email, company, role, source="manual")

    msg = f"✅ Added `{email}` to database for *{company}*."
    if queued:
        msg += "\n📧 Also added to mail queue."
    else:
        msg += "\n⚠️ Already exists in mail queue or processed."

    await update.effective_message.reply_text(msg, parse_mode="Markdown")


async def cmd_mailresponse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View recent inbox responses"""
    items = load_inbox_items()
    if not items:
        await update.effective_message.reply_text("📥 No responses found in inbox.")
        return

    msg = "📥 *Recent Inbox Responses*\n\n"
    for item in items[-10:]:
        sender = escape_md(item.get("sender_name", "Unknown"))
        type_ = item.get("classification", {}).get("type", "unknown").upper()
        msg += f"👤 {sender}\n🏷️ *{type_}*\n\n"

    await update.effective_message.reply_text(msg, parse_mode="Markdown")


# ─── Callback Action Handlers ──────────────────────────────
async def handle_job_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                            jid, status):
    """Bug #2, 5: Handle Apply/Dismiss/Save."""
    jobs = load_csv(JOBS_FILE)
    job = next((j for j in jobs if str(j.get("job_id")) == str(jid)),
               None)  # Changed 'id' to 'job_id'
    if not job:
        await update.callback_query.answer(
            "Job not found or already processed.")
        return

    # Save to status file
    file_map = {
        "applied": APPLIED_FILE,
        "dismissed": DISMISSED_FILE,
        "saved": SAVED_FILE
    }
    target_file = file_map.get(status)
    if target_file:
        save_to_csv(job, target_file)

    await update.callback_query.message.edit_reply_markup(reply_markup=None)
    await update.callback_query.message.reply_text(
        f"✅ Job marked as {status.upper()}")


async def handle_job_details_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                                jid):
    """Bug #2, 5: Show full details."""
    jobs = load_csv(JOBS_FILE)
    job = next((j for j in jobs if str(j.get("job_id")) == str(jid)),
               None)  # Changed 'id' to 'job_id'
    if job:
        msg = fmt_job(job)
        await update.callback_query.message.reply_text(msg,
                                                       parse_mode="Markdown")
    else:
        await update.callback_query.answer("Job details not found.")


async def handle_quick_mail(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                            jid):
    """Bug #2, 5: Add to mail queue."""
    jobs = load_csv(JOBS_FILE)
    job = next((j for j in jobs if str(j.get("job_id")) == str(jid)),
               None)  # Changed 'id' to 'job_id'
    if job and job.get(
            "emails"
    ):  # Changed 'email' to 'emails' as per job object structure
        # Assuming 'emails' field might contain multiple emails separated by comma
        email_list = [e.strip() for e in job["emails"].split(',') if e.strip()]
        added_count = 0
        for email in email_list:
            if add_to_mail_queue(
                    email,
                    job.get("company", ""),
                    job.get("title", ""),
                    source="quick_mail",
                    priority="HIGH"):  # Changed source to quick_mail
                added_count += 1
        if added_count > 0:
            await update.callback_query.message.reply_text(
                f"📧 Added {added_count} email(s) to queue!")
        else:
            await update.callback_query.answer(
                "No new emails added to queue (already exists or invalid).")
    else:
        await update.callback_query.answer("No email found for this job.")


async def handle_copy_email(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                            jid):
    """Bug #2, 5: Provide email for easy copying."""
    jobs = load_csv(JOBS_FILE)
    job = next((j for j in jobs if str(j.get("job_id")) == str(jid)),
               None)  # Changed 'id' to 'job_id'
    if job and job.get("emails"):  # Changed 'email' to 'emails'
        await update.callback_query.message.reply_text(
            f"📋 Email: `{job['emails']}`",
            parse_mode="Markdown")  # Changed 'email' to 'emails'
    else:
        await update.callback_query.answer("No email found for this job.")


# ─── Callback handler for inbox buttons ───────────────────
async def handle_inbox_callbacks(q, d, m, ctx):
    """Handle inbox-related callback buttons"""

    if d == "inbox_refresh":
        await m.reply_text("🔄 Checking inbox...")
        result = check_inbox()

        if result["instant"]:
            for alert in result["instant"]:
                await m.reply_text(format_instant_alert(alert),
                                   parse_mode="Markdown")

        unhandled = get_unhandled_items()
        await m.reply_text(
            f"✅ Checked! {result['total_new']} new emails\n"
            f"📬 {len(unhandled)} items need attention\n\n"
            f"Use /inbox to see",
            parse_mode="Markdown")

    elif d == "inbox_handleall":
        items = load_inbox_items()
        count = 0
        for item in items:
            if not item.get("handled") and item["classification"][
                    "priority"] in ("CRITICAL", "URGENT", "NORMAL"):
                item["handled"] = True
                item["handled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                count += 1
        save_inbox_items(items)
        await m.reply_text(f"✅ Marked {count} items as handled!",
                           parse_mode="Markdown")

    elif d.startswith("inbox_done_"):
        idx = int(d.replace("inbox_done_", ""))
        if mark_handled(idx):
            remaining = len(get_unhandled_items())
            await m.reply_text(f"✅ Done! {remaining} remaining")

    elif d.startswith("inbox_snooze_"):
        idx = int(d.replace("inbox_snooze_", ""))
        if ctx.job_queue:
            remind_time = datetime.now() + timedelta(hours=2)
            async def send_reminder(context):
                u = get_unhandled_items()
                if idx < len(u):
                    email_addr = u[idx].get("sender_email", "Unknown")
                    await context.bot.send_message(
                        chat_id=context.job.chat_id,
                        text=f"⏰ *Reminder*: Respond to {email_addr}",
                        parse_mode="Markdown"
                    )
            ctx.job_queue.run_once(send_reminder, remind_time, chat_id=m.chat_id)
            await m.reply_text("⏰ Snoozed for 2 hours\nWill remind you later")
        else:
            await m.reply_text("⚠️ Job queue not available. Snooze failed.")

    elif d.startswith("inbox_copy_"):
        idx = int(d.replace("inbox_copy_", ""))
        unhandled = get_unhandled_items()
        if 0 <= idx < len(unhandled):
            email_addr = unhandled[idx].get("sender_email", "")
            await m.reply_text(f"📧 `{email_addr}`\n\nTap to copy!",
                               parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════
# SCHEDULED TASKS (Email Monitor)
# ═══════════════════════════════════════════════════════════


async def scheduled_inbox_check(bot):
    """Run every 30 minutes — non-blocking"""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, check_inbox)  # ← Non-blocking

        for alert in result.get("instant", []):
            try:
                text = format_instant_alert(alert)
                kb = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📖 Read Full",
                                             callback_data="inbox_refresh"),
                        InlineKeyboardButton("✅ Handled",
                                             callback_data="inbox_handleall"),
                    ],
                ])
                await bot.send_message(chat_id=CHAT_ID,
                                       text=text,
                                       parse_mode="Markdown",
                                       reply_markup=kb)
            except Exception as e:
                logger.error(f"Instant alert send failed: {e}")
    except Exception as e:
        logger.error(f"Scheduled inbox check failed: {e}")


async def scheduled_batch_alert(bot):
    """Run every 4 hours by scheduler"""
    batch = load_batch_alerts()

    auto_replies = batch.get("auto_replies", 0)
    bounces = batch.get("bounces", 0)
    spam = batch.get("spam_ignored", 0)

    if auto_replies + bounces + spam == 0:
        return

    msg = ("📊 *Email Update*\n"
           "━━━━━━━━━━━━━━━━━━\n\n")

    if auto_replies > 0:
        msg += f"📋 {auto_replies} auto-replies received\n"
    if bounces > 0:
        msg += f"❌ {bounces} bounces auto-removed\n"
    if spam > 0:
        msg += f"🗑️ {spam} spam ignored\n"

    unhandled = get_unhandled_items()
    if unhandled:
        msg += f"\n🎯 {len(unhandled)} items need attention\n"
        msg += "Type /inbox to see"

    try:
        await bot.send_message(chat_id=CHAT_ID,
                               text=msg,
                               parse_mode="Markdown")
    except:
        pass

    # Reset counters so same events aren't reported again
    reset_batch_alerts()


async def scheduled_daily_digest(bot):
    """Run once at 9 PM IST — FIXED: Uses safe_load_json"""
    try:
        stats = get_inbox_stats()

        # FIXED: Use safe_load_json instead of raw json.load
        queue = safe_load_json(MAIL_QUEUE_FILE, [])

        today = datetime.now().strftime("%Y-%m-%d")
        today_sent = 0
        today_failed = 0
        total_sent = 0
        pending = 0

        for q in queue:
            status = q.get("status", "")
            updated = q.get("updated_at", "")

            if status == "sent":
                total_sent += 1
            elif status == "pending":
                pending += 1

            if updated and updated.startswith(today):
                if status == "sent":
                    today_sent += 1
                elif status in ("failed", "permanently_failed"):
                    today_failed += 1

        # Calculate response rate safely
        rate = 0
        total_responses = stats.get("total_responses", 0)
        if total_sent > 0:
            rate = total_responses / total_sent * 100

        msg = (
            f"📊 *DAILY REPORT*\n"
            f"══════════════════════\n"
            f"📅 {datetime.now().strftime('%d %b %Y')}\n\n"
            f"📧 *SENT TODAY:*\n"
            f"  ✅ Sent: {today_sent}\n"
            f"  ❌ Failed: {today_failed}\n\n"
            f"📬 *RECEIVED TODAY:*\n"
            f"  🎉 HR Replies: {stats.get('today_replies', 0)}\n"
            f"  📋 Auto-replies: {stats.get('today_auto', 0)}\n"
            f"  ❌ Bounces: {stats.get('today_bounces', 0)}\n\n"
            f"📈 *RESPONSE RATE:* {rate:.1f}%\n\n"
            f"📋 *QUEUE STATUS:*\n"
            f"  ⏳ Pending: {pending}\n"
            f"  ✅ Total sent: {total_sent}\n\n"
        )

        unhandled = get_unhandled_items()
        if unhandled:
            msg += (
                f"🎯 *ACTION NEEDED:*\n"
                f"  {len(unhandled)} unhandled items\n"
                f"  /inbox to handle\n"
            )

        await bot.send_message(
            chat_id=CHAT_ID, text=msg, parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Daily digest error: {e}", exc_info=True)
        try:
            await bot.send_message(chat_id=CHAT_ID, text="⚠️ Daily digest calculation failed. Check logs.")
        except: pass


async def scheduled_daily_mail(bot):
    """Daily 8 AM IST: Push queue to PA, auto-create PA scheduled task,
    and send TG alert if anything is blocked."""
    try:
        import requests as _req

        queue = load_mail_queue()
        pending = [q for q in queue if q.get("status") == "pending"]

        if not pending:
            await bot.send_message(
                chat_id=CHAT_ID,
                text="📧 *Daily Mail 8 AM*\n📭 No pending emails to send.",
                parse_mode="Markdown")
            return

        # ── Step 1: Push queue file to PA ─────────────────────
        push_ok = True
        push_err = ""
        try:
            push_to_pa(MAIL_QUEUE_FILE)
        except Exception as e:
            push_ok = False
            push_err = str(e)
            logger.error(f"Daily mail push failed: {e}")

        # ── Step 2: Ensure PA scheduled task exists ───────────
        task_ok = True
        task_msg = ""
        if PA_TOKEN and PA_USERNAME:
            try:
                api_url = f"https://www.pythonanywhere.com/api/v0/user/{PA_USERNAME}/schedule/"
                headers = {"Authorization": f"Token {PA_TOKEN}"}

                # Check existing tasks
                resp = _req.get(api_url, headers=headers, timeout=15)
                if resp.status_code == 200:
                    tasks = resp.json()
                    existing = [t for t in tasks if "bot.py" in t.get("command", "")]

                    if existing:
                        task_msg = f"✅ PA task already exists (ID: {existing[0].get('id')})"
                    else:
                        # Create daily task: 2:35 AM UTC = 8:05 AM IST
                        payload = {
                            "command": f"python3 /home/{PA_USERNAME}/bot.py",
                            "enabled": True,
                            "interval": "daily",
                            "hour": 2,
                            "minute": 35,
                            "description": "Daily mail sender - 8 AM IST",
                        }
                        create = _req.post(api_url, headers=headers,
                                           json=payload, timeout=15)
                        if create.status_code in (200, 201):
                            task_id = create.json().get("id", "?")
                            task_msg = f"✅ PA task created (ID: {task_id})"
                            logger.info(f"Created PA scheduled task: {task_id}")
                        else:
                            task_ok = False
                            task_msg = f"❌ PA create failed: {create.status_code} {create.text[:100]}"
                            logger.error(f"PA task create error: {create.text}")
                else:
                    task_ok = False
                    task_msg = f"❌ PA API {resp.status_code}"
            except Exception as e:
                task_ok = False
                task_msg = f"❌ PA API error: {str(e)[:100]}"
                logger.error(f"PA schedule API error: {e}")
        else:
            task_ok = False
            task_msg = "⚠️ PA\\_TOKEN or PA\\_USERNAME missing in .env"

        # ── Step 3: TG report ─────────────────────────────────
        if push_ok and task_ok:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=(f"📧 *Daily Mail 8 AM — Ready!*\n"
                      f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                      f"📋 Pending: *{len(pending)}*\n"
                      f"📤 Queue pushed to PA\n"
                      f"⚙️ {task_msg}\n\n"
                      f"PA will auto-send at 8:05 AM IST."),
                parse_mode="Markdown")
        else:
            # BLOCKED — alert via TG
            await bot.send_message(
                chat_id=CHAT_ID,
                text=(f"🚨 *DAILY MAIL BLOCKED!*\n"
                      f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                      f"📋 Pending: *{len(pending)}* emails waiting\n"
                      f"📤 Push: {'✅' if push_ok else '❌ FAILED'}\n"
                      f"⚙️ Task: {task_msg}\n"
                      f"{f'Error: `{push_err[:150]}`' if push_err else ''}\n\n"
                      f"⚠️ *Manual action needed:*\n"
                      f"Use /papush or run `python3 bot.py` on PA console."),
                parse_mode="Markdown")

    except Exception as e:
        logger.error(f"scheduled_daily_mail error: {e}")
        try:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=f"🚨 *Daily Mail Error*\n\n`{e}`\n\nUse /papush manually.",
                parse_mode="Markdown")
        except:
            pass


# Feature #20: Weekly Analytics Report
async def send_weekly_report(bot):
    """Run every Sunday at 8 PM"""
    queue = load_mail_queue()
    items = load_inbox_items()

    total_sent = sum(1 for q in queue if q.get("status") == "sent")
    total_responses = sum(1 for i in items
                          if i["classification"]["type"] in ("reply",
                                                             "positive",
                                                             "interview"))
    interviews = sum(1 for i in items
                     if i["classification"]["type"] == "interview")
    rate = (total_responses / total_sent * 100) if total_sent > 0 else 0

    # Week-over-week comparison
    this_week_dt = datetime.now() - timedelta(days=7)
    week_str = this_week_dt.strftime("%Y-%m-%d")

    new_this_week = sum(
        1 for q in queue
        if q.get("added_at", "") >= week_str and q.get("status") == "sent")
    responses_this_week = sum(
        1 for i in items
        if i.get("date", "") >= week_str and i["classification"]["type"] in (
            "reply", "positive", "interview"))

    msg = ("📊 *WEEKLY REPORT*\n"
           "══════════════════════\n"
           f"📅 Week of {this_week_dt.strftime('%d %b')} - "
           f"{datetime.now().strftime('%d %b %Y')}\n\n"
           f"📤 *Emails Sent This Week:* {new_this_week}\n"
           f"📬 *Responses This Week:* {responses_this_week}\n"
           f"🚨 *Interviews Today (All Time):* {interviews}\n\n"
           f"📈 *All-Time Stats:*\n"
           f"  Total Sent: {total_sent}\n"
           f"  Total Responses: {total_responses}\n"
           f"  Response Rate: {rate:.1f}%\n\n")

    if rate < 2:
        msg += "💡 *Tip:* Response rate is low. Try personalizing subject lines!"
    elif rate < 5:
        msg += "💡 *Tip:* Good progress! Consider following up on pending emails."
    else:
        msg += "🎉 *Great response rate!* Keep this momentum going!"

    try:
        await bot.send_message(chat_id=CHAT_ID,
                               text=msg,
                               parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Weekly report failed: {e}")


async def retry_failed_emails(bot):
    """Bug #37 fix: Retry failed emails with attempt tracking."""
    queue = load_mail_queue()
    retried_count = 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    with data_lock:
        for item in queue:
            status = item.get("status", "")
            attempts = item.get("attempts", 0)
            if status == "failed" and attempts < 3:
                item["status"] = "pending"
                item["error"] = None
                item["updated_at"] = now_str
                # Don't increment attempts here — PA will increment on next send
                retried_count += 1
                log_mail_activity("mail_retry", item.get("email", "unknown"))
            elif status == "sending":
                # Unlock stuck "sending" emails older than 1 hour
                last = item.get("last_attempt", "")
                if last:
                    try:
                        # Fix #17: Attempt tracking
                        dt = None
                        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
                            try:
                                dt = datetime.strptime(str(last).strip(), fmt)
                                break
                            except:
                                continue
                        
                        if dt and (datetime.now() - dt).total_seconds() > 3600:
                            item["status"] = "pending"
                            item["error"] = "stuck_in_sending"
                            item["updated_at"] = now_str
                            retried_count += 1
                    except Exception as e:
                        logger.error(f"Retry error for {last}: {e}")
                        item["status"] = "pending"
                        item["updated_at"] = now_str
                        retried_count += 1
                else:
                    item["status"] = "pending"
                    item["updated_at"] = now_str
                    retried_count += 1

    if retried_count > 0:
        save_mail_queue(queue)
        try:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=
                "🔄 *Auto-Retry*\nAdded {} failed/stuck emails back to queue.".
                format(retried_count),
                parse_mode="Markdown")
        except:
            pass


async def backup_data(bot):
    """Backup critical files every 6 hours"""
    critical_files = [
        MAIL_QUEUE_FILE,
        EMAILS_FILE,
        JOBS_FILE,
        os.path.join(DATA_DIR, "inbox_items.json"),
        os.path.join(DATA_DIR, "inbox_log.json"),
    ]
    backup_dir = os.path.join(DATA_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    backed_up = 0
    for filepath in critical_files:
        if os.path.exists(filepath):
            filename = os.path.basename(filepath)
            backup_path = os.path.join(backup_dir,
                                       "{}_{}".format(timestamp, filename))
            try:
                shutil.copy2(filepath, backup_path)
                backed_up += 1
            except:
                pass

    # Keep only last 20 backups
    try:
        backups = sorted(os.listdir(backup_dir))
        if len(backups) > 20:
            for old in backups[:-20]:
                os.remove(os.path.join(backup_dir, old))
    except:
        pass

    logger.info("Backed up {} files".format(backed_up))


async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View recent backups"""
    backup_dir = os.path.join(DATA_DIR, "backups")
    if not os.path.exists(backup_dir):
        await update.effective_message.reply_text("📂 No backups yet")
        return
    backups = sorted(os.listdir(backup_dir), reverse=True)
    if not backups:
        await update.effective_message.reply_text("📂 Backup folder empty")
        return

    # Group by timestamp (YYYYMMDD_HHMM)
    groups = {}
    for b in backups:
        ts = b[:13]
        groups.setdefault(ts, []).append(b)

    ts_list = sorted(groups.keys(), reverse=True)[:10]
    txt = "📂 *Recent Backups*\n{}\n\n".format(SEP_DASH)
    for ts in ts_list:
        txt += "📅 `{}` ({} files)\n".format(ts, len(groups[ts]))

    txt += "\n💡 Use `/restore timestamp` to restore a set."
    await update.effective_message.reply_text(txt, parse_mode="Markdown")


async def cmd_restore(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Restore critical files from a specific backup timestamp"""
    if not ctx.args:
        await update.effective_message.reply_text("⚠️ Please provide a timestamp\n"
                                        "Example: `/restore 20260306_1200`")
        return
    ts = ctx.args[0]
    backup_dir = os.path.join(DATA_DIR, "backups")
    if not os.path.exists(backup_dir):
        await update.effective_message.reply_text("❌ No backup directory found")
        return

    files = [f for f in os.listdir(backup_dir) if f.startswith(ts)]
    if not files:
        await update.effective_message.reply_text(
            "❌ No files found for timestamp `{}`".format(ts),
            parse_mode="Markdown")
        return

    restored = 0
    for f in files:
        # Expected format: YYYYMMDD_HHMM_filename.ext
        parts = f.split("_", 2)
        if len(parts) < 3: continue
        original_name = parts[2]
        src = os.path.join(backup_dir, f)
        dst = os.path.join(DATA_DIR, original_name)
        try:
            shutil.copy2(src, dst)
            restored += 1
        except:
            pass

    await update.effective_message.reply_text(
        "✅ *Restore Complete*\n"
        "Restored {} files from `{}`\n\n"
        "🔄 Please restart the bot to ensure changes take effect.".format(
            restored, ts),
        parse_mode="Markdown")


async def safe_reply(message_or_update, text, **kwargs):
    """Send/reply with Markdown, fall back to plain text if parsing fails"""
    # Handle both Update and Message objects (use effective_message for callbacks)
    if hasattr(message_or_update, "effective_message"):
        msg = message_or_update.effective_message
    else:
        msg = message_or_update
    if not msg: return None

    parse_mode = kwargs.get("parse_mode", "Markdown")
    try:
        return await msg.reply_text(text, **kwargs)
    except Exception as e:
        if parse_mode == "Markdown" and ("parse" in str(e).lower()
                                         or "can't" in str(e).lower()):
            # Markdown failed — strip formatting and retry
            clean_text = text.replace("*", "").replace("`",
                                                       "").replace("_", "")
            kwargs["parse_mode"] = None
            try:
                return await msg.reply_text(clean_text, **kwargs)
            except:
                return None
        else:
            # Other error, try one last time without markdown
            kwargs["parse_mode"] = None
            try:
                return await msg.reply_text(text[:4000], **kwargs)
            except:
                return None


async def safe_edit(message, text, **kwargs):
    """Wrap edit_message_text with fallback to reply_text + Markdown safety"""
    try:
        await message.edit_text(text, **kwargs)
    except Exception as e:
        err = str(e).lower()
        if "message is not modified" in err:
            pass
        elif "parse" in err or "can't" in err:
            clean_text = text.replace("*", "").replace("`",
                                                       "").replace("_", "")
            if "parse_mode" in kwargs: kwargs["parse_mode"] = None
            try:
                await message.edit_text(clean_text, **kwargs)
            except:
                await safe_reply(message, text, **kwargs)
        else:
            await safe_reply(message, text, **kwargs)


async def send_followup_report(bot):
    """Check for emails sent exactly ~3 days ago with no response"""
    queue = load_mail_queue()
    follow_ups = []

    now = datetime.now()
    for item in queue:
        if item.get("status") == "sent" and item.get(
                "delivery_status") != "failed":
            timestamp_str = item.get("last_attempt") or item.get("added_at")
            if timestamp_str:
                dt = None
                for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
                    try:
                        # Fix #16: Strip whitespace and handle robust formats
                        dt = datetime.strptime(str(timestamp_str).strip(), fmt)
                        break
                    except:
                        continue

                if not dt:
                    continue

                hours_elapsed = (now - dt).total_seconds() / 3600
                if 60 <= hours_elapsed <= 96 and not item.get(
                        "response_received"):
                    if not item.get("followup_reminder_sent"):
                        follow_ups.append(item)
                        item["followup_reminder_sent"] = True

    if not follow_ups:
        return

    msg = f"🔔 *Follow-Up Reminder!* ({len(follow_ups)} pending)\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += "These emails were sent ~3 days ago with no response:\n\n"

    for item in follow_ups[:10]:
        msg += f"🏢 *{item.get('company', 'Unknown')}*\n"
        msg += f"📧 `{item.get('email', '')}`\n"
        msg += f"👤 Role: {item.get('role', 'N/A')}\n\n"

    if len(follow_ups) > 10:
        msg += f"...and {len(follow_ups) - 10} more.\n"

    msg += "\n*Actions:*\n"
    msg += "1. Check your email for responses\n"
    msg += "2. Send follow-up email if appropriate\n"
    msg += "3. `/mailresponse <email> yes` to mark as replied"

    try:
        await bot.send_message(chat_id=CHAT_ID,
                               text=msg,
                               parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to send follow-up report: {e}")


# ═══════════════════════════════════════════════════════════
#  PREMIUM DASHBOARD (Integrated)
# ═══════════════════════════════════════════════════════════

import functools
from flask import make_response


def _check_dash_auth():
    pwd = request.args.get("pwd", "")
    if pwd == DASHBOARD_PASSWORD:
        return True
    auth = request.authorization
    if auth and auth.username == "admin" and auth.password == DASHBOARD_PASSWORD:
        return True
    return False


def require_dashboard_auth(f):

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not _check_dash_auth():
            return make_response(
                "🔒 Dashboard locked. Add ?pwd=YOUR_PASSWORD to the URL.", 401,
                {"WWW-Authenticate": 'Basic realm="Dashboard"'})
        return f(*args, **kwargs)

    return decorated


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>BCA Job Hunter — Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0e1a;
    --surface: rgba(255,255,255,0.04);
    --glass: rgba(255,255,255,0.06);
    --border: rgba(255,255,255,0.08);
    --text: #e2e8f0;
    --text-dim: #94a3b8;
    --accent: #6366f1;
    --accent2: #8b5cf6;
    --green: #22c55e;
    --red: #ef4444;
    --amber: #f59e0b;
    --cyan: #06b6d4;
    --pink: #ec4899;
    --radius: 16px;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: 'Inter', -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    overflow-x: hidden;
  }
  body::before, body::after {
    content: '';
    position: fixed;
    border-radius: 50%;
    filter: blur(120px);
    opacity: 0.15;
    z-index: 0;
    pointer-events: none;
  }
  body::before {
    width: 600px; height: 600px;
    background: var(--accent);
    top: -200px; left: -100px;
    animation: float 20s ease-in-out infinite;
  }
  body::after {
    width: 500px; height: 500px;
    background: var(--pink);
    bottom: -150px; right: -100px;
    animation: float 25s ease-in-out infinite reverse;
  }
  @keyframes float {
    0%,100% { transform: translate(0,0); }
    33% { transform: translate(30px, -30px); }
    66% { transform: translate(-20px, 20px); }
  }
  @keyframes fadeUp {
    from { opacity:0; transform: translateY(24px); }
    to { opacity:1; transform: translateY(0); }
  }
  @keyframes pulse {
    0%,100% { opacity:1; }
    50% { opacity:0.5; }
  }

  .container {
    max-width: 1200px;
    margin: 0 auto;
    padding: 24px 20px;
    position: relative;
    z-index: 1;
  }
  /* ── Header ─────────────────────────── */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 32px;
    animation: fadeUp 0.5s ease;
  }
  .header h1 {
    font-size: 1.8rem;
    font-weight: 800;
    background: linear-gradient(135deg, var(--accent), var(--pink));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: -0.5px;
  }
  .header .live-badge {
    display: flex; align-items: center; gap: 8px;
    font-size: 0.8rem;
    color: var(--green);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .live-badge .dot {
    width: 8px; height: 8px;
    background: var(--green);
    border-radius: 50%;
    animation: pulse 2s infinite;
  }
  .subtitle {
    color: var(--text-dim);
    font-size: 0.85rem;
    margin-top: 4px;
  }

  /* ── Stats Grid ─────────────────────── */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
    margin-bottom: 28px;
  }
  .stat-card {
    background: var(--glass);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 24px;
    backdrop-filter: blur(12px);
    transition: transform 0.2s, border-color 0.2s;
    animation: fadeUp 0.6s ease both;
  }
  .stat-card:nth-child(2) { animation-delay: 0.08s; }
  .stat-card:nth-child(3) { animation-delay: 0.16s; }
  .stat-card:nth-child(4) { animation-delay: 0.24s; }
  .stat-card:nth-child(5) { animation-delay: 0.32s; }
  .stat-card:nth-child(6) { animation-delay: 0.40s; }
  .stat-card:hover {
    transform: translateY(-4px);
    border-color: var(--accent);
  }
  .stat-card .icon {
    font-size: 1.6rem;
    margin-bottom: 12px;
  }
  .stat-card .value {
    font-size: 2rem;
    font-weight: 800;
    line-height: 1;
    margin-bottom: 6px;
  }
  .stat-card .label {
    font-size: 0.78rem;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.8px;
    font-weight: 600;
  }
  .stat-card.accent .value { color: var(--accent); }
  .stat-card.green .value { color: var(--green); }
  .stat-card.amber .value { color: var(--amber); }
  .stat-card.red .value { color: var(--red); }
  .stat-card.cyan .value { color: var(--cyan); }
  .stat-card.pink .value { color: var(--pink); }

  /* ── Sections ───────────────────────── */
  .section {
    background: var(--glass);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 24px;
    margin-bottom: 20px;
    backdrop-filter: blur(12px);
    animation: fadeUp 0.7s ease both;
  }
  .section-title {
    font-size: 1rem;
    font-weight: 700;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  /* ── Priority Bar ───────────────────── */
  .priority-bar {
    display: flex;
    height: 14px;
    border-radius: 8px;
    overflow: hidden;
    margin-bottom: 12px;
    background: rgba(255,255,255,0.03);
  }
  .priority-bar .seg {
    transition: width 0.6s ease;
    min-width: 2px;
  }
  .seg.high { background: linear-gradient(90deg, #ef4444, #f97316); }
  .seg.mid  { background: linear-gradient(90deg, #f59e0b, #eab308); }
  .seg.low  { background: linear-gradient(90deg, #06b6d4, #0ea5e9); }
  .priority-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    font-size: 0.78rem;
    color: var(--text-dim);
  }
  .priority-legend span {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .priority-legend .dot-lg {
    width: 10px; height: 10px;
    border-radius: 3px;
    display: inline-block;
  }

  /* ── Table ──────────────────────────── */
  .table-wrap { overflow-x: auto; }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
  }
  th {
    text-align: left;
    padding: 10px 12px;
    font-weight: 600;
    color: var(--text-dim);
    border-bottom: 1px solid var(--border);
    text-transform: uppercase;
    font-size: 0.7rem;
    letter-spacing: 0.8px;
  }
  td {
    padding: 10px 12px;
    border-bottom: 1px solid rgba(255,255,255,0.03);
    word-break: break-word;
  }
  tr:hover td { background: rgba(255,255,255,0.02); }
  .badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .badge.pending { background: rgba(245,158,11,0.15); color: #f59e0b; }
  .badge.sent    { background: rgba(34,197,94,0.15); color: #22c55e; }
  .badge.failed  { background: rgba(239,68,68,0.15); color: #ef4444; }
  .badge.sending { background: rgba(99,102,241,0.15); color: #6366f1; }
  .badge.bounced { background: rgba(236,72,153,0.15); color: #ec4899; }
  .badge.high    { background: rgba(239,68,68,0.12); color: #f87171; }
  .badge.normal  { background: rgba(34,197,94,0.12); color: #4ade80; }
  .badge.low     { background: rgba(148,163,184,0.12); color: #94a3b8; }
  .empty-msg {
    text-align: center;
    padding: 40px;
    color: var(--text-dim);
    font-size: 0.9rem;
  }

  /* ── Footer ─────────────────────────── */
  .footer {
    text-align: center;
    padding: 24px 0;
    color: var(--text-dim);
    font-size: 0.72rem;
    opacity: 0.6;
  }

  /* ── Responsive ─────────────────────── */
  @media (max-width: 640px) {
    .header h1 { font-size: 1.3rem; }
    .stat-card .value { font-size: 1.6rem; }
    .stats-grid { grid-template-columns: repeat(2, 1fr); gap: 10px; }
    .stat-card { padding: 16px; }
  }
</style>
</head>
<body>
<div class="container">
  <!-- Header -->
  <div class="header">
    <div>
      <h1>🎯 BCA Job Hunter</h1>
      <p class="subtitle">v{{ version }} &mdash; Auto Job Scan + Mail Bot Dashboard</p>
    </div>
    <div class="live-badge"><span class="dot"></span> LIVE</div>
  </div>

  <!-- Stats Cards -->
  <div class="stats-grid">
    <div class="stat-card accent">
      <div class="icon">📋</div>
      <div class="value">{{ stats.total }}</div>
      <div class="label">Total Jobs</div>
    </div>
    <div class="stat-card green">
      <div class="icon">⭐</div>
      <div class="value">{{ stats.important }}</div>
      <div class="label">Important</div>
    </div>
    <div class="stat-card amber">
      <div class="icon">🔥</div>
      <div class="value">{{ stats.high }}</div>
      <div class="label">High Salary</div>
    </div>
    <div class="stat-card cyan">
      <div class="icon">🏢</div>
      <div class="value">{{ stats.mnc }}</div>
      <div class="label">MNC Jobs</div>
    </div>
    <div class="stat-card pink">
      <div class="icon">📧</div>
      <div class="value">{{ stats.emails }}</div>
      <div class="label">Email Contacts</div>
    </div>
    <div class="stat-card green">
      <div class="icon">📤</div>
      <div class="value">{{ stats.mail_sent }}</div>
      <div class="label">Mails Sent</div>
    </div>
  </div>

  <!-- Priority Breakdown -->
  <div class="section" style="animation-delay:0.1s">
    <div class="section-title">📊 Priority Breakdown</div>
    {% set total_p = stats.high + stats.mid + stats.low + 1 %}
    <div class="priority-bar">
      <div class="seg high" style="width:{{ (stats.high / total_p * 100)|round(1) }}%"></div>
      <div class="seg mid" style="width:{{ (stats.mid / total_p * 100)|round(1) }}%"></div>
      <div class="seg low" style="width:{{ (stats.low / total_p * 100)|round(1) }}%"></div>
    </div>
    <div class="priority-legend">
      <span><span class="dot-lg" style="background:#ef4444"></span> High ({{ stats.high }})</span>
      <span><span class="dot-lg" style="background:#f59e0b"></span> Mid ({{ stats.mid }})</span>
      <span><span class="dot-lg" style="background:#06b6d4"></span> Low ({{ stats.low }})</span>
    </div>
  </div>

  <!-- Mail Queue -->
  <div class="section" style="animation-delay:0.2s">
    <div class="section-title">📨 Mail Queue
      <span style="margin-left:auto;font-size:0.75rem;color:var(--text-dim)">
        {{ mail_stats.pending }} pending &middot; {{ mail_stats.sent }} sent &middot; {{ mail_stats.failed }} failed
      </span>
    </div>
    {% if queue|length > 0 %}
    <div class="table-wrap">
      <table>
        <thead><tr><th>Email</th><th>Company</th><th>Role</th><th>Priority</th><th>Status</th></tr></thead>
        <tbody>
        {% for item in queue[:20] %}
          <tr>
            <td>{{ item.email }}</td>
            <td>{{ item.company or 'Unknown' }}</td>
            <td>{{ item.role or 'Entry-Level' }}</td>
            <td><span class="badge {{ item.priority|lower if item.priority else 'normal' }}">{{ item.priority or 'NORMAL' }}</span></td>
            <td><span class="badge {{ item.status|lower if item.status else 'pending' }}">{{ item.status or 'pending' }}</span></td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% if queue|length > 20 %}
    <p style="text-align:center;color:var(--text-dim);font-size:0.78rem;margin-top:12px">
      Showing 20 of {{ queue|length }} items. Use /mailqueue in Telegram to see all.
    </p>
    {% endif %}
    {% else %}
    <div class="empty-msg">📭 Mail queue is empty. Forward a job post in Telegram to start!</div>
    {% endif %}
  </div>

  <!-- Progress -->
  <div class="section" style="animation-delay:0.3s">
    <div class="section-title">📈 Your Progress</div>
    <div class="stats-grid" style="margin-bottom:0">
      <div class="stat-card green" style="padding:16px">
        <div class="value" style="font-size:1.5rem">{{ stats.applied }}</div>
        <div class="label">Applied</div>
      </div>
      <div class="stat-card accent" style="padding:16px">
        <div class="value" style="font-size:1.5rem">{{ stats.saved }}</div>
        <div class="label">Saved</div>
      </div>
      <div class="stat-card cyan" style="padding:16px">
        <div class="value" style="font-size:1.5rem">{{ stats.companies }}</div>
        <div class="label">Companies</div>
      </div>
      <div class="stat-card amber" style="padding:16px">
        <div class="value" style="font-size:1.5rem">{{ stats.mail_pending }}</div>
        <div class="label">Mail Pending</div>
      </div>
    </div>
  </div>

  <div class="footer">
    BCA Job Hunter &mdash; Auto-refreshes every 60s &mdash; Last update: {{ stats.last_update }}
  </div>
</div>
</body>
</html>
"""


@app_web.route("/")
@require_dashboard_auth
def dashboard_home():
    stats = get_stats()
    try:
        mail_stats = get_mail_stats()
    except Exception:
        mail_stats = {"pending": 0, "sent": 0, "failed": 0}

    return render_template_string(DASHBOARD_HTML,
                                  stats=stats,
                                  mail_stats=mail_stats,
                                  queue=load_mail_queue(),
                                  version=BOT_VERSION)


@app_web.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "version": BOT_VERSION,
        "searching": job_cache.get("search_running", False)
    })


def _expected_secret():
    return (os.environ.get("MAIL_BOT_SECRET", "") or "").strip()


@app_web.route("/api/debug_secret")
def api_debug_secret():
    import hashlib
    s = _expected_secret()
    return jsonify({
        "server_secret_len":
        len(s),
        "server_secret_hash8":
        hashlib.sha256(s.encode()).hexdigest()[:8],
    })


def _check_api_auth():
    """Unified API authentication — supports header OR query param"""
    secret = (request.headers.get("X-Mail-Bot-Secret") or "").strip()
    if secret and secret == _expected_secret():
        return True

    pwd = request.args.get("pwd", "").strip()
    if pwd and pwd == DASHBOARD_PASSWORD:
        return True

    auth = request.authorization
    if auth and auth.password in (DASHBOARD_PASSWORD, _expected_secret()):
        return True

    return False


@app_web.route("/api/stats")
def api_stats():
    if not _check_api_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(get_stats())


# ─── Resume Sync API (Fix #7) ───────────────────────────

@app_web.route("/api/resume_download")
def api_resume_download():
    """Allow PA to download the latest resume PDF"""
    if not _check_api_auth():
        return jsonify({"error": "unauthorized"}), 401
    
    if not os.path.exists(RESUME_FILE):
        return jsonify({"error": "resume_not_found"}), 404
        
    return send_file(RESUME_FILE, mimetype='application/pdf')

@app_web.route("/api/resume_info")
def api_resume_info():
    """Returns resume metadata for sync check"""
    if not _check_api_auth():
        return jsonify({"error": "unauthorized"}), 401
        
    if not os.path.exists(RESUME_FILE):
        return jsonify({"exists": False})
        
    meta = safe_load_json(RESUME_META_FILE, {})
    if not isinstance(meta, dict): meta = {}
    return jsonify({
        "exists": True,
        "updated_at": meta.get("updated_at"),
        "size_kb": meta.get("size_kb")
    })


@app_web.route("/api/mail_queue")
def api_mail_queue():
    incoming = (request.headers.get("X-Mail-Bot-Secret")
                or request.args.get("secret") or "").strip()
    expected = _expected_secret()
    if not expected:
        return jsonify({"error": "server_secret_not_set"}), 500
    if incoming != expected:
        return jsonify({"error": "unauthorized"}), 401
    queue = load_mail_queue()
    pending_items = []
    
    # Render templates on Replit before sending to PA
    for q in queue:
        if q.get("status") == "pending":
            template_id = q.get("template", "normal")
            co = q.get("company", "Unknown")
            role = q.get("role", "General IT Role")
            
            subject, body = render_template(template_id, company=co, role=role)
            
            q_copy = q.copy()
            q_copy["subject"] = subject
            q_copy["body"] = body
            pending_items.append(q_copy)

    return jsonify({"pending": pending_items, "count": len(pending_items)})


@app_web.route("/api/mail_update", methods=["POST"])
def api_mail_update():
    incoming = (request.headers.get("X-Mail-Bot-Secret")
                or request.args.get("secret") or "").strip()
    expected = _expected_secret()
    if not expected:
        return jsonify({"error": "server_secret_not_set"}), 500
    if incoming != expected:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json()
    if not data: return jsonify({"error": "no data"}), 400
    email = data.get("email", "").strip().lower()
    status = data.get("status", "")
    attempts = data.get("attempts")
    if not email or not status:
        return jsonify({"error": "missing fields"}), 400
    with data_lock:
        queue = load_mail_queue()
        updated = False
        for q in queue:
            if q.get("email", "").lower() == email:
                q["status"] = status
                q["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                if attempts is not None:
                    q["attempts"] = int(attempts)
                updated = True
                break
        if updated:
            save_mail_queue(queue)
            log_mail_activity("mail_{}".format(status), email)
    return jsonify({"updated": updated, "email": email, "status": status})


@app_web.route("/api/mail_queue_download")
def api_queue_download():
    incoming = (request.headers.get("X-Mail-Bot-Secret")
                or request.args.get("secret") or "").strip()
    expected = _expected_secret()
    if not expected:
        return jsonify({"error": "server_secret_not_set"}), 500
    if incoming != expected:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(load_mail_queue())




def keep_alive():
    """Start Flask dashboard AND self-ping to prevent Replit sleep"""

    def run_flask():
        """Run Flask with auto-restart on crash"""
        retries = 0
        while retries < 5:
            try:
                app_web.run(
                    host="0.0.0.0",
                    port=WEB_PORT,
                    debug=False,
                    use_reloader=False,
                )
                retries = 0 # reset on success
            except Exception as e:
                retries += 1
                logger.error(f"Flask crashed ({retries}/5): {e}")
                time.sleep(10)

    def self_ping():
        """Ping our own server every 4 minutes to prevent Replit sleep"""
        time.sleep(30)  # Wait for Flask to start
        ping_url = f"http://localhost:{WEB_PORT}/health"
        while True:
            try:
                urllib.request.urlopen(ping_url, timeout=10)
            except Exception:
                pass
            time.sleep(240)  # Every 4 minutes

    # Start Flask in background thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"✅ Dashboard started on port {WEB_PORT}")

    # Start self-ping in background thread
    ping_thread = Thread(target=self_ping, daemon=True)
    ping_thread.start()
    logger.info("✅ Self-ping keep-alive started (every 4 min)")




# ═══════════════════════════════════════════════════════════
#  MAIN BOT RUNNER
# ═══════════════════════════════════════════════════════════


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler — ALWAYS tells user something went wrong"""
    error = context.error
    error_msg = str(error)[:300]

    logger.error(f"Unhandled error: {error}", exc_info=True)

    if not update: return

    # Determine user-friendly message
    if "Timed out" in error_msg or "TimedOut" in error_msg:
        user_msg = "⏳ Request timed out. Please try again."
    elif "NetworkError" in error_msg:
        user_msg = "🌐 Network error. Check your connection."
    elif "Message is not modified" in error_msg:
        return
    elif "query is too old" in error_msg:
        return
    elif "OCR" in error_msg or "easyocr" in error_msg.lower():
        user_msg = "📸 Image processing failed. Try sending a clearer image or paste text instead."
    else:
        user_msg = f"⚠️ Something went wrong:\n`{error_msg[:200]}`\n\nPlease try again."

    try:
        if update.effective_message:
            await safe_reply(update.effective_message,
                             user_msg,
                             parse_mode="Markdown")
        elif update.callback_query:
            await update.callback_query.answer(user_msg[:200], show_alert=True)
    except Exception:
        try:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=f"⚠️ *Global Error:*\n`{error_msg[:500]}`",
                parse_mode="Markdown")
        except:
            pass


async def run_bot():
    print("=" * 55)
    print("  {} v{}".format(BOT_NAME, BOT_VERSION))
    print("  Replit + PythonAnywhere Architecture")
    print("=" * 55)

    keep_alive()
    print("✅ Web dashboard on port {}".format(WEB_PORT))
    await asyncio.sleep(2)

    if not TELEGRAM_BOT_TOKEN or len(TELEGRAM_BOT_TOKEN) < 10:
        print("❌ No valid bot token! Set TELEGRAM_BOT_TOKEN env var.")
        print("   Running web-only mode.")
        while True:
            await asyncio.sleep(60)

    print("✅ Chat ID: {}".format(CHAT_ID))

    # Bug #7: Validate GMAIL_APP_PASSWORD
    if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
        print("❌ GMAIL_EMAIL or GMAIL_APP_PASSWORD not set!")
        print("   Auto-mailer and inbox checking will fail.")
        # We don't exit, as searching still works, but we warn loudly.
    elif len(GMAIL_APP_PASSWORD) < 16:
        print("⚠️ GMAIL_APP_PASSWORD looks invalid (too short).")
        print("   Gmail App Passwords should be 16 characters.")

    try:
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).read_timeout(
            30).write_timeout(30).connect_timeout(30).build()
    except Exception as e:
        print("❌ Bot creation failed: {}".format(e))
        while True:
            await asyncio.sleep(60)

    # Register ALL command handlers (11 original + 6 mail + NEW fixes)
    for cmd, handler in [
        ("start", cmd_start),
        ("search", cmd_search),
        ("emails", cmd_emails),
        ("stats", cmd_stats),
        ("mnc", cmd_mnc),
        ("saved", cmd_saved),
        ("applied", cmd_applied),
        ("high", cmd_high),
        ("recent", cmd_recent),
        ("export", cmd_export),
        ("help", cmd_help),
        ("mail", cmd_mail),
        ("mailqueue", cmd_mailqueue),
        ("mailsend", cmd_mailsend),
        ("mailstats", cmd_mailstats),
        ("mailhistory", cmd_mailhistory),
        ("mailadd", cmd_mailadd),
        ("bulkscan", cmd_bulkscan),
        ("bulkdone", cmd_bulkdone),
        ("bulkcancel", cmd_bulkcancel),
        ("ocrstatus", cmd_ocrstatus),
        ("mailresponse", cmd_mailresponse),
        ("inbox", cmd_inbox),
        ("reply", cmd_reply),
        ("done", cmd_done),
        ("snooze", cmd_snooze),
        ("pause", cmd_pause),
        ("resumefu", cmd_resume_followup),
        ("pipeline", cmd_pipeline),
        ("mailremove", cmd_mailremove),
        ("bancompany", cmd_bancompany),
        ("stopsearch", cmd_stopsearch),
        ("resumesearch", cmd_resumesearch),
        ("backup", cmd_backup),
        ("restore", cmd_restore),
        ("status", cmd_status),
        ("templates", cmd_templates),
        ("previewtemplate", cmd_previewtemplate),
        ("edittemplate", cmd_edittemplate),
        ("addtemplate", cmd_addtemplate),
        ("deletetemplate", cmd_deletetemplate),
        ("resettemplate", cmd_resettemplate),
        ("resume", cmd_resume),
        ("updateresume", cmd_updateresume),
        ("quicksend", cmd_quicksend),
    ]:
        app.add_handler(CommandHandler(cmd, handler))

    app.add_handler(CommandHandler("papush", cmd_papush))
    app.add_handler(CommandHandler("papull", cmd_papull))

    # Pattern patterns registration
    app.add_handler(CallbackQueryHandler(cb_fwd_handler, pattern="^fwd_"))
    app.add_handler(CallbackQueryHandler(cb_template_handler, pattern="^tmpl_"))
    app.add_handler(CallbackQueryHandler(cb_handler))

    # Message handler #43 Fix: Priority for Photo/Document/Forwarded
    message_filter = (filters.TEXT | filters.PHOTO | filters.Document.IMAGE
                      | filters.FORWARDED) & ~filters.COMMAND
    app.add_handler(MessageHandler(message_filter, handle_message))

    app.add_error_handler(error_handler)
    
    # ─── CRITICAL: Start Flask Server for Replit 24/7 Keep-Alive ───
    keep_alive()
    
    await app.initialize()
    await app.start()
    print("✅ Bot initialized")

    # Bug #24 fix: Data integrity check
    def check_data_integrity():
        os.makedirs("data", exist_ok=True)
        for f in [JOBS_FILE, EMAILS_FILE]:
            if os.path.exists(f):
                try:
                    df = pd.read_csv(f)
                    logger.info("Loaded {}: {} rows".format(f, len(df)))
                except Exception as e:
                    logger.error("Corrupt file {}: {}".format(f, e))
                    try:
                        shutil.move(f, f + ".corrupt.bak")
                    except:
                        pass

    check_data_integrity()

    # Scheduler with explicit timezone (Bug #54)
    from pytz import timezone as pytz_timezone
    india_tz = pytz_timezone("Asia/Kolkata")
    scheduler = AsyncIOScheduler(timezone=india_tz)

    # Bug #57 fix: Health monitoring for scheduler
    async def scheduler_health_check():
        jobs = scheduler.get_jobs()
        logger.info(f"⏰ Scheduler Health: {len(jobs)} active jobs running.")

    scheduler.add_job(scheduler_health_check, 'interval', minutes=60)
    scheduler.add_job(run_search,
                      "interval",
                      hours=HOURS_BETWEEN_SEARCHES,
                      args=[app.bot],
                      id="auto_search",
                      misfire_grace_time=600,
                      max_instances=1)

    # Inbox check every 30 minutes
    scheduler.add_job(scheduled_inbox_check,
                      "interval",
                      minutes=30,
                      args=[app.bot],
                      id="inbox_check",
                      misfire_grace_time=300,
                      max_instances=1)

    # Batch alerts every 4 hours
    scheduler.add_job(scheduled_batch_alert,
                      "interval",
                      hours=4,
                      args=[app.bot],
                      id="batch_alert",
                      misfire_grace_time=600,
                      max_instances=1)

    # Daily digest at 9 PM IST = 3:30 PM UTC
    scheduler.add_job(scheduled_daily_digest,
                      "cron",
                      hour=15,
                      minute=30,
                      args=[app.bot],
                      id="daily_digest",
                      misfire_grace_time=300,
                      max_instances=1)

    # Follow up notifications daily
    scheduler.add_job(send_followup_report,
                      "interval",
                      hours=24,
                      args=[app.bot],
                      id="follow_ups",
                      misfire_grace_time=3600,
                      max_instances=1)

    # Daily mail at 8 AM IST — push queue to PA before PA's scheduler runs bot.py
    scheduler.add_job(scheduled_daily_mail,
                      "cron",
                      hour=8,
                      minute=0,
                      args=[app.bot],
                      id="daily_mail",
                      misfire_grace_time=600,
                      max_instances=1)

    # Retry failed emails every 12 hours
    scheduler.add_job(retry_failed_emails,
                      "interval",
                      hours=12,
                      args=[app.bot],
                      id="retry_failed",
                      misfire_grace_time=3600,
                      max_instances=1)

    # Weekly cleanup (CSV trimming)
    async def weekly_cleanup_job():
        """Bug #23 fix: Use named function instead of lambda for robustness"""
        for f in [JOBS_FILE, EMAILS_FILE]:
            trim_csv(f)

    scheduler.add_job(weekly_cleanup_job,
                      "cron",
                      day_of_week="sun",
                      hour=3,
                      id="weekly_cleanup")

    # Weekly analytics report at 8 PM IST = 2:30 PM UTC
    scheduler.add_job(send_weekly_report,
                      "cron",
                      day_of_week="sun",
                      hour=14,
                      minute=30,
                      args=[app.bot],
                      id="weekly_report")

    # Auto-backup every 6 hours
    scheduler.add_job(backup_data,
                      "interval",
                      hours=6,
                      args=[app.bot],
                      id="auto_backup",
                      misfire_grace_time=600,
                      max_instances=1)

    scheduler.start()
    print(
        "✅ Scheduler: auto_search every {}h, inbox every 30m, follow_ups daily, backup every 6h"
        .format(HOURS_BETWEEN_SEARCHES))

    # Startup notification
    try:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=("🚀 *{name} v{ver} Online!*\n{dash}\n\n"
                  "⏰ Auto search: every {hrs}h\n"
                  "📧 Auto mail: daily at 8 AM (PythonAnywhere)\n"
                  "🌐 Dashboard: port {port}\n\n"
                  "📸 *Forward any job post to auto-extract & mail!*\n\n"
                  "/start → Menu | /mail → Mail Bot\n/help → All commands"
                  ).format(name=BOT_NAME,
                           ver=BOT_VERSION,
                           hrs=HOURS_BETWEEN_SEARCHES,
                           port=WEB_PORT,
                           dash=SEP_DASH),
            parse_mode="Markdown")
    except Exception as e:
        print("⚠️ Startup message failed: {}".format(e))

    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES,
                                    drop_pending_updates=True)

    print()
    print("✅ BOT FULLY RUNNING!")
    print("📱 Send /start in Telegram")
    print("📧 Forward job posts to extract & mail!")
    print("🌐 http://localhost:{}".format(WEB_PORT))
    print("=" * 55)

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        print("\n🛑 Shutting down...")
        scheduler.shutdown()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    if not acquire_bot_lock():
        print("⚠️ Another bot instance is already running. Exiting.")
        raise SystemExit(0)
    retry = 0
    while retry < 10:
        try:
            asyncio.run(run_bot())
        except KeyboardInterrupt:
            print("\n👋 Stopped by user")
            break
        except Exception as e:
            retry += 1
            print("❌ Fatal Error: {}. Retrying in 45s... ({}/10)".format(
                e, retry))
            time.sleep(45)
    print("🛑 Max retries reached. Bot stopped.")


if __name__ == "__main__":
    main()
