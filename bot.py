# ============================================
# PYTHONANYWHERE SCRIPT v3.0 — FINAL OVERHAUL
# Reads from Replit API, sends emails with dynamic templates & resume sync
# ============================================

import smtplib, json, time, random
import urllib.request, urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime

# ─── Config (all from env vars) ──────────────────────────
import os

USERNAME = os.environ.get("PA_USERNAME", "akshat7081")
BASE_DIR = f"/home/{USERNAME}"

# Native .env parser (zero-dependency)
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()
YOUR_EMAIL = os.environ.get("GMAIL_EMAIL", "")
YOUR_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
YOUR_NAME = os.environ.get("YOUR_NAME", "Akshat Tripathi")
PHONE = os.environ.get("PHONE", "+91-7081484808")
LINKEDIN = os.environ.get("LINKEDIN", "linkedin.com/in/akshattripathi7081")
GITHUB = os.environ.get("GITHUB", "github.com/akshat7081")
UNIVERSITY = os.environ.get("UNIVERSITY", "Guru Gobind Singh Indraprastha University, New Delhi")
DEGREE = os.environ.get("DEGREE", "BCA")

PA_FREE_TIER = os.environ.get("PA_FREE_TIER", "true").lower() == "true"
USERNAME = os.environ.get("PA_USERNAME", "akshat7081")
# Folder on PA for script data
BASE_DIR = f"/home/{USERNAME}"
RESUME_FILE = os.path.join(BASE_DIR, "resume.pdf") 
LOG_FILE = os.path.join(BASE_DIR, "sent_log.txt")
REPORT_FILE = os.path.join(BASE_DIR, "daily_report.txt")
ERROR_LOG = os.path.join(BASE_DIR, "error_log.txt")
LOCK_FILE = os.path.join(BASE_DIR, "mail_bot.lock")

# REPLIT CONNECTION
REPLIT_URL = os.environ.get("REPLIT_URL", "").rstrip("/")
MAIL_BOT_SECRET = os.environ.get("MAIL_BOT_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# LIMITS
MAX_ATTEMPTS = 3
DAILY_LIMIT = 40 if not PA_FREE_TIER else 25

# ─── Helpers ──────────────────────────────────────────────

def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return False 
        except: pass
    try:
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except: return False

def release_lock():
    if os.path.exists(LOCK_FILE):
        try: os.remove(LOCK_FILE)
        except: pass

def log_error(email, error_msg):
    try:
        with open(ERROR_LOG, "a") as f:
            f.write(f"{datetime.now().isoformat()} | {email} | {error_msg}\n")
    except: pass

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID: return
    # Max size 4000 to be safe
    for i in range(0, len(msg), 4000):
        chunk = msg[i:i+4000]
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": CHAT_ID,
                "text": chunk,
                "parse_mode": "Markdown"
            }).encode()
            urllib.request.urlopen(url, data, timeout=10)
        except: pass

# ─── Data Sync ────────────────────────────────────────────

def get_sent_emails():
    """Load previously sent emails to prevent PA double-sending if Windows overwrites queue."""
    if not os.path.exists(LOG_FILE): return set()
    with open(LOG_FILE, "r") as f:
        return set(line.split()[0].strip().lower() for line in f if line.strip())

def fetch_queue():
    queue_file = os.path.join(BASE_DIR, "mail_queue.json")
    if not os.path.exists(queue_file): 
        print("No local mail_queue.json found.")
        return []
    try:
        with open(queue_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        sent = get_sent_emails()
        pending = []
        for q in data:
            if q.get("status") == "pending" and q.get("email", "").lower() not in sent:
                pending.append(q)
        return pending
    except Exception as e:
        print(f"Local queue read error: {e}")
        return []

def update_status(email, status, attempts=None):
    # Status updates back to Windows are disabled to avoid proxy 403 blocks.
    # State is maintained locally via LOG_FILE to prevent duplicates.
    pass

# ─── Email Engine (v3.0 Templates) ────────────────────────

def get_email_content(template_id, company, role):
    """Generate subject and body based on template (Fix #2, #6)"""

    # Template 1: Research Associate
    if template_id == "research":
        subject = f"Application for Research Associate Role - {YOUR_NAME}"
        body = f"""Dear Hiring Team,

I am writing to express my strong interest in the Research Associate position at your organization. With a background in {DEGREE} and a keen interest in data-driven research and technical documentation, I am confident in my ability to contribute to your research projects.

My Proficiency includes:
- Python for Data Analysis & Web Scraping
- Technical Writing & Documentation
- Advanced SQL for Data Retrieval
- Quantitative & Qualitative Research Methodology

I am an immediate joiner and eager to discuss how my skill set aligns with your organization's requirements. My resume is attached for your review.

Best regards,
{YOUR_NAME}
{LINKEDIN}"""

    # Template 2: Data Analytics
    elif template_id == "analytics":
        subject = f"Data Analyst/MIS Associate Application | {YOUR_NAME} | {DEGREE}"
        body = f"""Hi Team,

I am seeking an entry-level opportunity in Data Analytics at your organization. As a {DEGREE} graduate with strong foundations in SQL, Excel, and Python, I am passionate about turning raw data into meaningful business insights.

Key Technical Skills:
- SQL (Joins, Subqueries, Optimization)
- Python (Pandas, NumPy, Matplotlib)
- Microsoft Excel (VLOOKUP, Pivots, Macros)
- Power BI / Tableau (Basics)

I am available for an immediate start and would welcome the opportunity to interview with your team. Please find my resume attached.

Regards,
{YOUR_NAME}
Phone: {PHONE}"""

    # Template 3: Normal / General IT (Indian Standard)
    else:
        subject = f"Application for Entry-Level Opportunity - {YOUR_NAME} (BCA Fresher)"
        body = f"""Respected Hiring Manager,

I am {YOUR_NAME}, a recent {DEGREE} graduate from {UNIVERSITY}. I am applying for entry-level opportunities at your organization as advertised.

My Skill Set:
- Web Development (HTML/CSS, JavaScript)
- Python Scripting & Automation
- Basic Database Management (SQL)
- IT Support & Quality Assurance

I am a quick learner and an immediate joiner (no notice period). I have attached my resume and I am available for an interview at your earliest convenience.

Thank you for your consideration.

Warm regards,
{YOUR_NAME}
{PHONE} | {LINKEDIN}"""

    return subject, body

def send_email(server, to_email, company, role, template_id="normal"):
    try:
        if company in ("Unknown Company", "N/A", "", "nan", None):
            company = "your esteemed organization"

        subject, body = get_email_content(template_id, company, role)

        msg = MIMEMultipart()
        msg['From'] = f"{YOUR_NAME} <{YOUR_EMAIL}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        if os.path.exists(RESUME_FILE):
            with open(RESUME_FILE, "rb") as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'attachment; filename="resume.pdf"')
                msg.attach(part)

        server.sendmail(YOUR_EMAIL, to_email, msg.as_string())
        return True
    except Exception as e:
        log_error(to_email, str(e))
        return False

def get_smtp():
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=30)
        server.starttls()
        server.login(YOUR_EMAIL, YOUR_APP_PASSWORD)
        return server
    except Exception as e:
        print(f"SMTP Login Failed: {e}")
        return None

# ─── Main Logic ───────────────────────────────────────────

def main():
    if not acquire_lock(): return
    try:
        print(f"🚀 Started: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        queue = fetch_queue()
        to_send = queue
        if not to_send:
            print("No pending emails.")
            send_telegram("ℹ️ No pending emails in queue.")
            return

        server = get_smtp()
        if not server: return

        # ── INITIAL DELAY: Random between 9 and 11 minutes ──
        # Makes the 8:05 AM start look completely human and scattered.
        initial_wait = random.randint(540, 660) # 9 to 11 mins
        print(f"⏳ Waiting {initial_wait//60} mins before sending first email...")
        time.sleep(initial_wait)

        sent_count = 0
        failed_count = 0
        for item in to_send[:DAILY_LIMIT]:
            email = item.get("email")
            company = item.get("company")
            role = item.get("role")
            template = item.get("template", "normal")
            attempts = item.get("attempts", 0)

            # Mark as sending
            update_status(email, "sending")
            
            # Send
            if send_email(server, email, company, role, template):
                update_status(email, "sent")
                sent_count += 1
                with open(LOG_FILE, "a") as f: f.write(f"{email}\n")
            else:
                new_att = attempts + 1
                status = "failed" if new_att < MAX_ATTEMPTS else "permanently_failed"
                update_status(email, status, new_att)
                failed_count += 1
            
            # ── EMAIL GAP: Random between 3 and 6 minutes ──
            gap = random.randint(180, 360)
            time.sleep(gap)

        server.quit()
        
        # Report
        remaining = len(to_send) - sent_count - failed_count
        report_msg = f"✅ *Sent: {sent_count}* | ❌ *Failed: {failed_count}*\n"
        if remaining > 0:
            report_msg += f"⏳ Queue: {remaining} remaining."
        else:
            report_msg += "🎉 All items processed!"
        send_telegram(report_msg)
        
        # Write local report
        try:
            with open(REPORT_FILE, 'a') as f:
                f.write(f"\n--- {datetime.now().strftime('%Y-%m-%d %H:%M')} ---\n")
                f.write(f"Sent: {sent_count} | Failed: {failed_count} | Remaining: {remaining}\n")
        except: pass

    finally:
        release_lock()

    print(f"\nDone! Sent: {sent_count} | Failed: {failed_count}")



if __name__ == "__main__":
    main()