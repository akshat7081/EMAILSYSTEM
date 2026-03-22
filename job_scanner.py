# ============================================================
# JOB SCANNER v2.0 — LinkedIn/Indeed Email Extractor
# Uses python-jobspy (FREE, UNLIMITED, NO API KEY NEEDED)
# Scrapes LinkedIn + Indeed → extracts HR emails → sends to TG
# ============================================================
# ZERO SETUP REQUIRED — jobspy is already installed!
# Just run: /scan on Telegram
# ============================================================

import os, json, re, hashlib, time, logging
import urllib.request, urllib.parse
from datetime import datetime, timedelta

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

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")

DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
SEEN_JOBS_FILE = os.path.join(DATA_DIR, "seen_jobs.json")
SCAN_LOG_FILE  = os.path.join(DATA_DIR, "scan_log.json")
QUEUE_FILE     = os.path.join(BASE_DIR, "mail_queue.json")
PENDING_FILE   = os.path.join(DATA_DIR, "pending_actions.json")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scanner")

# ═══════════════════════════════════════════════════════════
# SEARCH CONFIG — Based on your resume skills
# ═══════════════════════════════════════════════════════════

SEARCH_QUERIES = [
    # ── Data & Analytics ──
    "data analyst",
    "data analyst fresher",
    "junior data analyst",
    "business analyst",
    "MIS analyst",
    "MIS executive",
    "data entry analyst",
    "Excel data analyst",
    "SQL data analyst",

    # ── Research ──
    "research analyst",
    "research associate",
    "research assistant",
    "market research analyst",

    # ── QA & Testing ──
    "software QA tester",
    "QA analyst",
    "QA engineer fresher",
    "manual testing fresher",
    "software tester",

    # ── Development (BCA-relevant) ──
    "junior python developer",
    "junior web developer",
    "python developer fresher",
    "HTML CSS developer",
    "frontend developer fresher",
    "junior software developer",
    "SQL developer fresher",
    "BCA fresher",

    # ── IT Support & Operations ──
    "IT support executive",
    "IT helpdesk",
    "technical support executive",
    "system administrator fresher",
    "IT executive",
    "IT coordinator",
    "desktop support engineer",

    # ── General / Back Office ──
    "data entry operator",
    "back office executive BCA",
    "computer operator",
    "office executive BCA",

    # ── Remote-specific ──
    "remote data analyst India",
    "remote QA tester India",
    "remote python developer India",
    "work from home data analyst",
    "work from home IT support",
]

LOCATIONS = [
    "Gurugram, India",
    "Delhi, India",
    "Noida, India",
    "New Delhi, India",
    "Ghaziabad, India",
    "Faridabad, India",
    "India",  # Catches all-India + remote postings
]

MAX_DAYS_OLD = 60  # 2 months window
RESULTS_PER_QUERY = 25  # jobspy is free — go massive! (40 queries × 7 locs × 25 = 7000 potential)

# ═══════════════════════════════════════════════════════════
# EMAIL EXTRACTION — Smart HR email finder
# ═══════════════════════════════════════════════════════════

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

# Skip these — not real HR contacts
SKIP_DOMAINS = {
    "example.com", "test.com", "email.com", "domain.com", "company.com",
    "sentry.io", "github.com", "gitlab.com", "w3.org", "schema.org",
    "googleusercontent.com", "gstatic.com", "googleapis.com",
    "apple.com", "microsoft.com", "mozilla.org", "w3schools.com",
    "stackoverflow.com", "wikipedia.org", "wikimedia.org",
    "linkedin.com", "indeed.com", "naukri.com", "monster.com",
    "glassdoor.com", "instagram.com", "facebook.com", "twitter.com",
    "youtube.com", "google.com",
}

# Spammy TLDs
SPAMMY_TLDS = {".xyz", ".top", ".buzz", ".click", ".tk", ".ga", ".gq", ".ml", ".cf", ".work", ".icu"}

# HR-like email patterns (boost priority)
HR_PATTERNS = [
    r"^hr@", r"^hiring@", r"^recruit", r"^careers@", r"^jobs@",
    r"^talent@", r"^apply@", r"^placement@", r"^resume@",
    r"^cv@", r"^staffing@", r"^humanresource",
]

def extract_emails_from_text(text):
    """Extract valid HR-like emails from job description."""
    if not text:
        return []
    raw = EMAIL_RE.findall(text)
    valid = []
    seen = set()
    for e in raw:
        e = e.lower().strip().rstrip(".")
        if e in seen:
            continue
        seen.add(e)
        domain = e.split("@")[1] if "@" in e else ""

        # Skip blacklisted domains
        if domain in SKIP_DOMAINS:
            continue
        # Skip spammy TLDs
        if any(domain.endswith(tld) for tld in SPAMMY_TLDS):
            continue
        # Skip image-like
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg")):
            continue
        # Skip noreply
        if "noreply" in e or "no-reply" in e or "donotreply" in e:
            continue
        # Skip very short local parts
        local = e.split("@")[0]
        if len(local) < 3:
            continue

        valid.append(e)

    # Sort: HR-like emails first
    def hr_score(email):
        for pat in HR_PATTERNS:
            if re.match(pat, email.split("@")[0]):
                return 0  # Top priority
        return 1
    valid.sort(key=hr_score)

    return valid


def classify_email_quality(email):
    """Classify email as hr/corporate/free."""
    domain = email.split("@")[1] if "@" in email else ""
    local = email.split("@")[0]

    # HR-specific
    for pat in HR_PATTERNS:
        if re.match(pat, local):
            return "🟢 HR"

    # Free email
    free_domains = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
                    "rediffmail.com", "ymail.com", "live.com", "icloud.com"}
    if domain in free_domains:
        return "🟡 Personal"

    # Corporate
    return "🔵 Corporate"


# ═══════════════════════════════════════════════════════════
# TELEGRAM API
# ═══════════════════════════════════════════════════════════

def tg_api(method, data=None):
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
        logger.error(f"TG API error: {e}")
        return {}

def send_tg(text, reply_markup=None):
    data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_api("sendMessage", data)


# ═══════════════════════════════════════════════════════════
# DEDUPLICATION
# ═══════════════════════════════════════════════════════════

def load_seen():
    if os.path.exists(SEEN_JOBS_FILE):
        try:
            with open(SEEN_JOBS_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return []

def save_seen(seen):
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(seen[-10000:], f, indent=2)

def is_seen(job_id):
    return job_id in load_seen()

def mark_seen(job_id):
    seen = load_seen()
    if job_id not in seen:
        seen.append(job_id)
        save_seen(seen)

def is_already_in_queue(email):
    if not os.path.exists(QUEUE_FILE):
        return False
    try:
        with open(QUEUE_FILE, "r") as f:
            queue = json.load(f)
        return any(item.get("email", "").lower() == email.lower() for item in queue)
    except:
        return False


# ═══════════════════════════════════════════════════════════
# PENDING ACTIONS (shared with bridge_app.py)
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
# JOBSPY SCRAPER — FREE, UNLIMITED, NO API KEY
# ═══════════════════════════════════════════════════════════

def scrape_jobs_batch(query, location, results_wanted=20):
    """
    Scrape LinkedIn + Indeed using python-jobspy.
    FREE — no API key needed. No request limits.
    Returns list of job dicts.
    """
    try:
        from jobspy import scrape_jobs
        import pandas as pd

        logger.info(f"Scraping: '{query}' in '{location}'...")

        df = scrape_jobs(
            site_name=["linkedin", "indeed"],
            search_term=query,
            location=location,
            results_wanted=results_wanted,
            hours_old=MAX_DAYS_OLD * 24,  # Convert days to hours
            country_indeed="India",
            verbose=0,
        )

        if df is None or df.empty:
            return []

        jobs = []
        for _, row in df.iterrows():
            jobs.append({
                "job_id": str(row.get("id", "")),
                "title": str(row.get("title", "Unknown")),
                "company": str(row.get("company", "Unknown")),
                "location": str(row.get("location", location)),
                "description": str(row.get("description", "")),
                "job_url": str(row.get("job_url", "")),
                "date_posted": str(row.get("date_posted", "")),
                "site": str(row.get("site", "unknown")),
                "job_type": str(row.get("job_type", "")),
                "salary_source": str(row.get("min_amount", "")),
                "min_salary": row.get("min_amount"),
                "max_salary": row.get("max_amount"),
                "currency": str(row.get("currency", "INR")),
                "is_remote": bool(row.get("is_remote", False)),
                "company_url": str(row.get("company_url", "")),
            })

        return jobs

    except ImportError:
        logger.error("python-jobspy not installed! pip install python-jobspy")
        return []
    except Exception as e:
        logger.error(f"Scrape error for '{query}' in '{location}': {e}")
        return []


# ═══════════════════════════════════════════════════════════
# JOB PROCESSOR — Filter + Extract + Send
# ═══════════════════════════════════════════════════════════

def process_job(job):
    """
    Process a single job from jobspy.
    Returns enriched dict if job has emails, else None.
    """
    job_id = job.get("job_id", "")
    title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")
    location = job.get("location", "India")
    description = job.get("description", "")
    job_url = job.get("job_url", "")
    site = job.get("site", "unknown")
    date_posted = job.get("date_posted", "")

    # Generate unique ID if missing
    if not job_id or job_id == "nan":
        job_id = hashlib.md5(f"{title}{company}{location}".encode()).hexdigest()[:12]

    # Skip if already seen
    if is_seen(job_id):
        return None

    # Calculate days old
    days_old = "?"
    if date_posted and date_posted != "nan" and date_posted != "":
        try:
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%m/%d/%Y"):
                try:
                    posted_date = datetime.strptime(str(date_posted).strip()[:10], fmt)
                    days_old = (datetime.now() - posted_date).days
                    break
                except ValueError:
                    continue
            if isinstance(days_old, int) and days_old > MAX_DAYS_OLD:
                mark_seen(job_id)
                return None
        except:
            pass

    # Extract emails from description
    emails = extract_emails_from_text(description)

    # Also check URL for mailto links
    if job_url and "mailto:" in str(job_url).lower():
        emails.extend(extract_emails_from_text(str(job_url)))

    # Deduplicate emails
    emails = list(dict.fromkeys(emails))

    # Filter already-in-queue
    new_emails = [e for e in emails if not is_already_in_queue(e)]

    if not new_emails:
        mark_seen(job_id)
        return None

    # Build salary string
    salary_str = ""
    min_sal = job.get("min_salary")
    max_sal = job.get("max_salary")
    if min_sal and str(min_sal) != "nan" and str(min_sal) != "None":
        try:
            if max_sal and str(max_sal) != "nan" and str(max_sal) != "None":
                salary_str = f"₹{int(float(min_sal)):,} - ₹{int(float(max_sal)):,}"
            else:
                salary_str = f"₹{int(float(min_sal)):,}+"
        except:
            pass

    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "emails": new_emails,
        "days_old": days_old,
        "site": site.capitalize() if site else "Unknown",
        "salary": salary_str,
        "job_url": job_url if job_url and str(job_url) != "nan" else "",
        "description_preview": (description or "")[:400],
        "is_remote": job.get("is_remote", False),
    }


def send_job_to_telegram(job_info):
    """Send a rich job card to Telegram with approve/reject buttons."""
    data_key = hashlib.md5(f"{job_info['job_id']}{time.time()}".encode()).hexdigest()[:6]

    # Store in pending for bridge_app.py
    pending = load_pending()
    pending[data_key] = {
        "emails": job_info["emails"],
        "company": job_info["company"],
        "role": job_info["title"],
        "source": "scanner",
        "ts": time.time(),
    }
    save_pending(pending)

    # Mark as seen
    mark_seen(job_info["job_id"])

    # Build email list with quality badges
    email_lines = []
    for e in job_info["emails"]:
        badge = classify_email_quality(e)
        email_lines.append(f"  {badge} `{e}`")
    email_list = "\n".join(email_lines)

    remote_tag = " 🏠 Remote" if job_info.get("is_remote") else ""
    salary_line = f"💰 Salary: *{job_info['salary']}*\n" if job_info.get("salary") else ""
    url_line = f"🔗 [View Job]({job_info['job_url']})\n" if job_info.get("job_url") else ""

    # Clean description preview
    preview = job_info.get("description_preview", "")[:250]
    preview = preview.replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")

    msg = (
        f"🔍 *New Job Found!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💼 *{job_info['title']}*\n"
        f"🏢 {job_info['company']}\n"
        f"📍 {job_info['location']}{remote_tag}\n"
        f"📅 Posted: {job_info['days_old']} days ago\n"
        f"🌐 Source: {job_info['site']}\n"
        f"{salary_line}"
        f"{url_line}"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📧 *HR Emails ({len(job_info['emails'])}):\n*"
        f"{email_list}\n\n"
        f"📝 _{preview}..._\n\n"
        f"👇 *Approve to send application?*"
    )

    buttons = [[
        {"text": "✅ Approve — Send Email", "callback_data": f"jobok_{data_key}"},
        {"text": "❌ Skip", "callback_data": f"jobno_{data_key}"},
    ]]

    return send_tg(msg, reply_markup={"inline_keyboard": buttons})


# ═══════════════════════════════════════════════════════════
# MAIN SCANNER
# ═══════════════════════════════════════════════════════════

def run_scan(queries=None, locations=None, notify_start=True):
    """
    Main scan function.
    Uses python-jobspy — FREE, UNLIMITED, NO API KEY.
    Can be triggered by:
      - /scan command via Telegram
      - PA scheduled task (daily)
    """
    if queries is None:
        queries = SEARCH_QUERIES
    if locations is None:
        locations = LOCATIONS

    if notify_start:
        query_list = ", ".join(queries[:5])
        if len(queries) > 5:
            query_list += f"... +{len(queries)-5} more"

        send_tg(
            f"🔍 *Job Scan Started...*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📋 Queries: {len(queries)} ({query_list})\n"
            f"📍 Locations: {', '.join(locations)}\n"
            f"📅 Filter: Last {MAX_DAYS_OLD} days\n"
            f"📧 Only jobs with HR email addresses\n\n"
            f"🆓 Using jobspy (free, unlimited)\n"
            f"⏳ This may take 3-5 minutes..."
        )

    total_found = 0
    jobs_with_emails = 0
    errors = 0
    scans_done = 0

    for loc in locations:
        for query in queries:
            scans_done += 1
            try:
                logger.info(f"[{scans_done}] Scanning: '{query}' in '{loc}'")
                jobs = scrape_jobs_batch(query, loc, RESULTS_PER_QUERY)
                total_found += len(jobs)

                for job in jobs:
                    result = process_job(job)
                    if result:
                        jobs_with_emails += 1
                        send_job_to_telegram(result)
                        time.sleep(1.5)  # Telegram rate limit

                # Be polite to LinkedIn/Indeed — don't hammer
                time.sleep(3)

            except Exception as e:
                errors += 1
                logger.error(f"Scan error for '{query}' in '{loc}': {e}")
                time.sleep(2)

    # ── Summary ──
    summary = (
        f"📊 *Scan Complete!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔍 Searches Done: {scans_done}\n"
        f"📋 Total Jobs Found: {total_found}\n"
        f"📧 Jobs with HR Emails: *{jobs_with_emails}*\n"
    )
    if errors:
        summary += f"❌ Errors: {errors}\n"
    if jobs_with_emails == 0:
        summary += "\nℹ️ No new jobs with HR email addresses found.\nTry again later or use /scan with a custom query."
    else:
        summary += f"\n✅ {jobs_with_emails} job(s) sent above!\nTap *Approve* → Pick template → Send instantly or schedule."

    send_tg(summary)

    # ── Log ──
    try:
        log = []
        if os.path.exists(SCAN_LOG_FILE):
            with open(SCAN_LOG_FILE, "r") as f:
                log = json.load(f)
        log.append({
            "timestamp": datetime.now().isoformat(),
            "searches": scans_done,
            "total_found": total_found,
            "with_emails": jobs_with_emails,
            "errors": errors,
        })
        with open(SCAN_LOG_FILE, "w") as f:
            json.dump(log[-100:], f, indent=2)
    except:
        pass

    return {
        "found": total_found,
        "with_emails": jobs_with_emails,
        "errors": errors,
        "searches": scans_done,
    }


# ─── CLI Entry Point (for PA scheduled task) ──────────────

if __name__ == "__main__":
    import sys
    print(f"🚀 Job Scanner v2.0 started: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"🆓 Using python-jobspy (free, unlimited, no API key)")

    if len(sys.argv) > 1:
        custom_queries = [" ".join(sys.argv[1:])]
        print(f"Custom query: {custom_queries}")
        result = run_scan(queries=custom_queries)
    else:
        result = run_scan()

    print(f"\nDone! Found: {result['found']} | With Emails: {result['with_emails']}")
