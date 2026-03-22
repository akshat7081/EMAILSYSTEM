# ============================================
# PYTHONANYWHERE SCRIPT v4.0 — SCHEDULED SENDER
# Reads mail_queue.json, sends emails with
# dynamic templates, resume, and auto follow-ups
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

import random as _rnd

def _build_sender_pool():
    pool = []
    e1 = os.environ.get("GMAIL_EMAIL", "")
    p1 = os.environ.get("GMAIL_APP_PASSWORD", "")
    if e1 and p1: pool.append((e1, p1))
    e2 = os.environ.get("GMAIL_EMAIL_2", "")
    p2 = os.environ.get("GMAIL_APP_PASSWORD_2", "")
    if e2 and p2: pool.append((e2, p2))
    e3 = os.environ.get("GMAIL_EMAIL_3", "")
    p3 = os.environ.get("GMAIL_APP_PASSWORD_3", "")
    if e3 and p3: pool.append((e3, p3))
    return pool

SENDER_POOL = _build_sender_pool()

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
    """Load previously sent emails to prevent PA double-sending."""
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
            is_pending = q.get("status") == "pending"
            is_followup = q.get("template") == "followup"
            is_not_sent = q.get("email", "").lower() not in sent
            
            # Allow follow-ups to bypass the deduplication check
            if is_pending and (is_not_sent or is_followup):
                pending.append(q)
        return pending
    except Exception as e:
        print(f"Local queue read error: {e}")
        return []

def update_status(email, status, attempts=None):
    if not REPLIT_URL or not MAIL_BOT_SECRET: return
    try:
        url = f"{REPLIT_URL}/api/mail_update"
        data = urllib.parse.urlencode({
            "email": email,
            "status": status,
            "secret": MAIL_BOT_SECRET
        }).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Failed to sync status to Replit for {email}: {e}")

# ─── Email Engine v5.0 — Clean Templates ─────────────────

def _make_greeting(email_addr):
    """Always use generic greeting — no name detection."""
    return "Hello Ma'am/Sir,"


def _signature_block():
    """Professional signature with contact links."""
    return (
        f"Regards,\n"
        f"{YOUR_NAME}\n"
        f"{PHONE}\n"
        f"LinkedIn: {LINKEDIN}\n"
        f"GitHub: {GITHUB}"
    )


def get_email_content(template_id, company, role, to_email=""):
    """Generate subject and body — clean templates, no company/employee names."""
    greeting = _make_greeting(to_email)
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

    else:
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

def send_email(to_email, company, role, template_id="normal"):
    if not SENDER_POOL:
        log_error(to_email, "No Gmail credentials configured")
        return False

    try:
        if company in ("Unknown Company", "N/A", "", "nan", None):
            company = "your organization"
        subject, body = get_email_content(template_id, company, role, to_email)

        senders = list(SENDER_POOL)
        _rnd.shuffle(senders)

        last_error = ""
        for sender_email, sender_pass in senders:
            try:
                msg = MIMEMultipart()
                msg['From'] = f"{YOUR_NAME} <{sender_email}>"
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

                server = smtplib.SMTP('smtp.gmail.com', 587, timeout=30)
                server.starttls()
                server.login(sender_email, sender_pass)
                server.sendmail(sender_email, to_email, msg.as_string())
                server.quit()

                return True
            except Exception as e:
                last_error = str(e)
                print(f"  ⚠️ Failed via {sender_email}: {e}. Trying next...")
                continue
        
        log_error(to_email, f"All senders failed: {last_error}")
        return False

    except Exception as e:
        log_error(to_email, str(e))
        return False

# ─── Main Logic ───────────────────────────────────────────

FOLLOWUP_AFTER_DAYS = 4  # Send follow-up 4 days after original (user requested)

def send_followups():
    """
    Automatic Follow-Up System:
    - Scans mail_queue.json for emails with status='sent'
    - If sent 4+ days ago AND followup_sent is not True, send follow-up
    - SKIPS emails where response_received is True (reply-aware)
    - Only sends 1 follow-up per email, ever
    """
    queue_file = os.path.join(BASE_DIR, "mail_queue.json")
    if not os.path.exists(queue_file):
        return 0

    try:
        with open(queue_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        return 0

    now = datetime.now()
    followup_count = 0
    modified = False

    for item in data:
        # Only follow up on successfully sent emails
        if item.get("status") != "sent":
            continue
        # Skip if already followed up
        if item.get("followup_sent"):
            continue
        # ── NEW: Skip if a response/reply was already received ──
        if item.get("response_received"):
            continue

        # Check age: must be at least FOLLOWUP_AFTER_DAYS old
        sent_date_str = item.get("updated_at") or item.get("added_at") or ""
        if not sent_date_str:
            continue

        try:
            # Try multiple date formats
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    sent_date = datetime.strptime(sent_date_str.strip(), fmt)
                    break
                except ValueError:
                    continue
            else:
                continue

            days_since = (now - sent_date).days
            if days_since < FOLLOWUP_AFTER_DAYS:
                continue
        except:
            continue

        email = item.get("email", "")
        company = item.get("company", "your organization")
        role = item.get("role", "the open position")

        if not email:
            continue

        # Send follow-up using the followup template
        try:
            if send_email(email, company, role, "followup"):
                item["followup_sent"] = True
                item["followup_date"] = now.strftime("%Y-%m-%d %H:%M")
                followup_count += 1
                modified = True
                print(f"  📩 Follow-up sent to {email}")
                send_telegram(f"📩 *Follow-Up Sent*\nTo: `{email}`\nCompany: {company}\n(Original sent {days_since} days ago)")

                # Human-like gap between follow-ups
                gap = random.randint(120, 240)  # 2-4 mins
                time.sleep(gap)
        except Exception as e:
            print(f"  ❌ Follow-up failed for {email}: {e}")

    # Save updated queue with followup flags
    if modified:
        try:
            with open(queue_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            # Also sync back to Replit
            if REPLIT_URL and MAIL_BOT_SECRET:
                try:
                    sync_url = f"{REPLIT_URL}/api/mail_queue_sync"
                    payload = urllib.parse.urlencode({
                        "secret": MAIL_BOT_SECRET,
                        "queue": json.dumps(data)
                    }).encode()
                    req = urllib.request.Request(sync_url, data=payload, method="POST")
                    urllib.request.urlopen(req, timeout=15)
                except:
                    pass
        except:
            pass

    return followup_count


def main():
    if not acquire_lock(): return
    try:
        print(f"🚀 Started: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        queue = fetch_queue()
        to_send = queue
        
        if not SENDER_POOL:
            print("❌ SMTP connection failed - No credentials.")
            return

        sent_count = 0
        failed_count = 0

        if to_send:
            # ── INITIAL DELAY: Random between 9 and 11 minutes ──
            initial_wait = random.randint(540, 660)
            print(f"⏳ Waiting {initial_wait//60} mins before sending first email...")
            time.sleep(initial_wait)

            for item in to_send[:DAILY_LIMIT]:
                email = item.get("email")
                company = item.get("company")
                role = item.get("role")
                template = item.get("template", "normal")
                attempts = item.get("attempts", 0)

                update_status(email, "sending")
                
                if send_email(email, company, role, template):
                    update_status(email, "sent")
                    sent_count += 1
                    with open(LOG_FILE, "a") as f: f.write(f"{email}\n")
                    send_telegram(f"✅ *Mail Sent*\nTo: `{email}`\nCompany: {company or 'N/A'}\nRole: {role or 'N/A'}")
                else:
                    new_att = attempts + 1
                    status = "failed" if new_att < MAX_ATTEMPTS else "permanently_failed"
                    update_status(email, status, new_att)
                    failed_count += 1
                
                gap = random.randint(180, 360)
                time.sleep(gap)
        else:
            print("No pending emails.")

        # ── AUTOMATIC FOLLOW-UP PHASE ──────────────────────
        print("\n📩 Starting follow-up phase...")
        followup_count = send_followups()
        

        # ── Count emails awaiting follow-up ──
        queue_file = os.path.join(BASE_DIR, "mail_queue.json")
        awaiting_followup = 0
        try:
            with open(queue_file, "r", encoding="utf-8") as f:
                all_data = json.load(f)
            now = datetime.now()
            for item in all_data:
                if item.get("status") == "sent" and not item.get("followup_sent") and not item.get("response_received"):
                    sd_str = item.get("updated_at") or item.get("added_at", "")
                    if sd_str:
                        try:
                            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                                try:
                                    sd = datetime.strptime(sd_str.strip(), fmt)
                                    break
                                except ValueError:
                                    continue
                            else:
                                continue
                            if (now - sd).days >= FOLLOWUP_AFTER_DAYS:
                                awaiting_followup += 1
                        except:
                            pass
        except:
            pass

        # ── DAILY SUMMARY REPORT ──────────────────────────
        remaining = len(to_send) - sent_count - failed_count if to_send else 0
        
        report_msg = (
            f"📋 *Daily Email Report*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ *Sent:* {sent_count}\n"
            f"❌ *Failed:* {failed_count}\n"
        )
        if followup_count > 0:
            report_msg += f"📩 *Follow-ups Sent:* {followup_count}\n"
        if awaiting_followup > 0:
            report_msg += f"⏰ *Awaiting Follow-up:* {awaiting_followup}\n"
        report_msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        if remaining > 0:
            report_msg += f"⏳ Queue Remaining: {remaining}\n"
        else:
            if to_send:
                report_msg += "🎉 All items processed!\n"
            else:
                report_msg += "ℹ️ No pending emails in queue.\n"
        report_msg += f"\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')} IST"
        
        send_telegram(report_msg)
        
        # Write local report
        try:
            with open(REPORT_FILE, 'a') as f:
                f.write(f"\n--- {datetime.now().strftime('%Y-%m-%d %H:%M')} ---\n")
                f.write(f"Sent: {sent_count} | Failed: {failed_count} | Follow-ups: {followup_count} | Awaiting: {awaiting_followup} | Remaining: {remaining}\n")
        except: pass

    finally:
        release_lock()

    print(f"\nDone! Sent: {sent_count} | Failed: {failed_count} | Follow-ups: {followup_count}")



if __name__ == "__main__":
    main()