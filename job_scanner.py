# ============================================================
# JOB SCANNER v1.0 — LinkedIn/Indeed Email Extractor
# Uses JSearch API (RapidAPI free: 500 req/month)
# Finds jobs with HR emails → sends to Telegram for approval
# ============================================================
# SETUP: Get free API key from https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
#   1. Sign up at rapidapi.com (free)
#   2. Subscribe to JSearch (free tier)
#   3. Copy your X-RapidAPI-Key
#   4. Add to .env: RAPIDAPI_KEY=your_key_here
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

BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID      = os.environ.get("CHAT_ID", "")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")

DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
SEEN_JOBS_FILE   = os.path.join(DATA_DIR, "seen_jobs.json")
SCAN_LOG_FILE    = os.path.join(DATA_DIR, "scan_log.json")
QUEUE_FILE       = os.path.join(BASE_DIR, "mail_queue.json")
PENDING_FILE     = os.path.join(DATA_DIR, "pending_actions.json")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scanner")

# ─── Search Configuration (based on your resume skills) ─────

SEARCH_QUERIES = [
    "data analyst",
    "research analyst",
    "research associate",
    "business analyst",
    "MIS analyst",
    "MIS executive",
    "software QA",
    "QA tester",
    "junior developer",
    "junior python developer",
    "web developer fresher",
    "IT support executive",
    "data entry analyst",
]

# Gurugram, Delhi NCR locations
LOCATIONS = [
    "Gurugram, Haryana, India",
    "Delhi, India",
    "Noida, Uttar Pradesh, India",
    "New Delhi, India",
]

MAX_DAYS_OLD = 15  # Only jobs posted within last 15 days
MAX_RESULTS_PER_QUERY = 10

# ─── Email Extraction ──────────────────────────────────────

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

# Domains to skip (not real HR contacts)
SKIP_DOMAINS = {
    "example.com", "test.com", "email.com", "domain.com", "company.com",
    "sentry.io", "github.com", "gitlab.com", "w3.org", "schema.org",
    "googleusercontent.com", "gstatic.com", "googleapis.com",
    "apple.com", "microsoft.com", "mozilla.org", "w3schools.com",
    "stackoverflow.com", "wikipedia.org", "wikimedia.org",
}

# Spammy TLDs
SPAMMY_TLDS = {".xyz", ".top", ".buzz", ".click", ".tk", ".ga", ".gq", ".ml", ".cf"}

def extract_emails_from_text(text):
    """Extract valid HR-like emails from job description text."""
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
        # Skip very short local parts (likely auto-generated)
        local = e.split("@")[0]
        if len(local) < 3:
            continue
        valid.append(e)
    return valid


# ─── Telegram API ──────────────────────────────────────────

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


# ─── Deduplication ─────────────────────────────────────────

def load_seen():
    if os.path.exists(SEEN_JOBS_FILE):
        try:
            with open(SEEN_JOBS_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return []

def save_seen(seen):
    # Keep last 2000
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(seen[-2000:], f, indent=2)

def is_seen(job_id):
    seen = load_seen()
    return job_id in seen

def mark_seen(job_id):
    seen = load_seen()
    if job_id not in seen:
        seen.append(job_id)
        save_seen(seen)

def is_already_in_queue(email):
    """Check if email is already in mail queue."""
    if not os.path.exists(QUEUE_FILE):
        return False
    try:
        with open(QUEUE_FILE, "r") as f:
            queue = json.load(f)
        for item in queue:
            if item.get("email", "").lower() == email.lower():
                return True
    except:
        pass
    return False


# ─── Pending Actions (shared with bridge_app.py) ──────────

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


# ─── JSearch API (RapidAPI) ────────────────────────────────

JSEARCH_HOST = "jsearch.p.rapidapi.com"

def jsearch_query(query, location, page=1):
    """
    Search for jobs using JSearch API.
    Free tier: 500 requests/month.
    Returns list of job dicts.
    """
    if not RAPIDAPI_KEY:
        logger.error("RAPIDAPI_KEY not set! Get one free at rapidapi.com")
        return []

    params = urllib.parse.urlencode({
        "query": f"{query} in {location}",
        "page": str(page),
        "num_pages": "1",
        "date_posted": "month",  # Last 30 days (we'll filter further)
        "remote_jobs_only": "false",
        "employment_types": "FULLTIME,INTERN,PARTTIME",
    })

    url = f"https://{JSEARCH_HOST}/search?{params}"
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": JSEARCH_HOST,
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode())
        return data.get("data", [])
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.warning("Rate limited. Waiting 60s...")
            time.sleep(60)
        elif e.code == 403:
            logger.error("API key invalid or quota exceeded!")
        else:
            logger.error(f"JSearch HTTP error {e.code}: {e.reason}")
        return []
    except Exception as e:
        logger.error(f"JSearch error: {e}")
        return []


def jsearch_estimated_salaries(title, location):
    """Get estimated salary for a role (bonus info)."""
    if not RAPIDAPI_KEY:
        return None
    params = urllib.parse.urlencode({
        "job_title": title,
        "location": location,
        "radius": "100",
    })
    url = f"https://{JSEARCH_HOST}/estimated-salary?{params}"
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": JSEARCH_HOST,
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())
        salaries = data.get("data", [])
        if salaries:
            return salaries[0]
    except:
        pass
    return None


# ─── Job Processing ───────────────────────────────────────

def process_job(job):
    """
    Process a single job result from JSearch.
    Returns dict with extracted info or None if not useful.
    """
    job_id = job.get("job_id", "")
    title = job.get("job_title", "Unknown Role")
    company = job.get("employer_name", "Unknown")
    location = job.get("job_city", "") or job.get("job_state", "") or "India"
    description = job.get("job_description", "")
    apply_link = job.get("job_apply_link", "")
    posted_ts = job.get("job_posted_at_timestamp")
    source = job.get("job_publisher", "Unknown")
    job_type = job.get("job_employment_type", "FULLTIME")
    is_remote = job.get("job_is_remote", False)
    min_salary = job.get("job_min_salary")
    max_salary = job.get("job_max_salary")
    currency = job.get("job_salary_currency", "INR")

    # Skip if already seen
    if not job_id:
        job_id = hashlib.md5(f"{title}{company}{location}".encode()).hexdigest()[:12]
    if is_seen(job_id):
        return None

    # Check age: must be under MAX_DAYS_OLD
    if posted_ts:
        try:
            posted_date = datetime.fromtimestamp(posted_ts)
            days_old = (datetime.now() - posted_date).days
            if days_old > MAX_DAYS_OLD:
                mark_seen(job_id)
                return None
        except:
            days_old = -1
    else:
        days_old = -1

    # Extract emails from description
    emails = extract_emails_from_text(description)

    # Also check in apply link (sometimes email-based apply)
    if apply_link and "mailto:" in apply_link.lower():
        mailto_emails = extract_emails_from_text(apply_link)
        emails.extend(mailto_emails)

    # Deduplicate
    emails = list(dict.fromkeys(emails))

    # Filter out emails already in queue
    new_emails = [e for e in emails if not is_already_in_queue(e)]

    if not new_emails:
        mark_seen(job_id)
        return None

    # Build salary string
    salary_str = ""
    if min_salary and max_salary:
        salary_str = f"₹{int(min_salary):,} - ₹{int(max_salary):,} {currency}"
    elif min_salary:
        salary_str = f"₹{int(min_salary):,}+ {currency}"

    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "emails": new_emails,
        "days_old": days_old if days_old >= 0 else "?",
        "source": source,
        "job_type": job_type,
        "is_remote": is_remote,
        "salary": salary_str,
        "apply_link": apply_link,
        "description_preview": (description or "")[:300],
    }


def send_job_to_telegram(job_info):
    """Send a job card to Telegram with approve/reject buttons."""
    data_key = hashlib.md5(f"{job_info['job_id']}{time.time()}".encode()).hexdigest()[:6]

    # Store in pending for bridge_app.py to handle callbacks
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

    # Build message
    email_list = "\n".join(f"  📧 `{e}`" for e in job_info["emails"])
    remote_tag = " 🏠 Remote" if job_info.get("is_remote") else ""
    salary_line = f"💰 Salary: *{job_info['salary']}*\n" if job_info.get("salary") else ""

    msg = (
        f"🔍 *New Job Found!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💼 *{job_info['title']}*\n"
        f"🏢 Company: *{job_info['company']}*\n"
        f"📍 Location: {job_info['location']}{remote_tag}\n"
        f"📅 Posted: {job_info['days_old']} days ago\n"
        f"🌐 Source: {job_info['source']}\n"
        f"{salary_line}"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📧 *HR Emails Found ({len(job_info['emails'])}):\n*"
        f"{email_list}\n\n"
        f"📝 *Preview:*\n"
        f"_{job_info['description_preview'][:200]}..._\n\n"
        f"👇 *Approve to send application?*"
    )

    buttons = [[
        {"text": "✅ Approve — Send Email", "callback_data": f"jobok_{data_key}"},
        {"text": "❌ Skip", "callback_data": f"jobno_{data_key}"},
    ]]

    return send_tg(msg, reply_markup={"inline_keyboard": buttons})


# ─── Main Scanner ─────────────────────────────────────────

def run_scan(queries=None, locations=None, notify_start=True):
    """
    Main scan function. Can be called from:
    - PA scheduled task (daily at 7 AM)
    - /scan command via bridge_app.py
    """
    if not RAPIDAPI_KEY:
        send_tg(
            "❌ *JSearch API Key Missing!*\n\n"
            "To enable job scanning:\n"
            "1. Go to [rapidapi.com](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)\n"
            "2. Sign up (free)\n"
            "3. Subscribe to JSearch (free tier: 500 req/month)\n"
            "4. Copy your API key\n"
            "5. Add to .env: `RAPIDAPI_KEY=your_key_here`\n"
            "6. Reload the web app"
        )
        return {"found": 0, "with_emails": 0, "error": "No API key"}

    if queries is None:
        queries = SEARCH_QUERIES
    if locations is None:
        locations = LOCATIONS

    if notify_start:
        send_tg(
            f"🔍 *Job Scan Started...*\n"
            f"📋 Queries: {len(queries)}\n"
            f"📍 Locations: {len(locations)}\n"
            f"📅 Filter: Last {MAX_DAYS_OLD} days with HR emails"
        )

    total_found = 0
    jobs_with_emails = 0
    errors = 0
    api_calls = 0

    for loc in locations:
        for query in queries:
            try:
                api_calls += 1
                logger.info(f"Scanning: '{query}' in {loc}")
                jobs = jsearch_query(query, loc)
                total_found += len(jobs)

                for job in jobs:
                    result = process_job(job)
                    if result:
                        jobs_with_emails += 1
                        send_job_to_telegram(result)
                        # Small delay between TG messages
                        time.sleep(1)

                # Rate limit: pause between API calls
                time.sleep(2)

            except Exception as e:
                errors += 1
                logger.error(f"Scan error for '{query}' in {loc}: {e}")

    # Summary
    summary = (
        f"📊 *Scan Complete!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔍 API Calls: {api_calls}\n"
        f"📋 Total Jobs Found: {total_found}\n"
        f"📧 Jobs with HR Emails: *{jobs_with_emails}*\n"
    )
    if errors:
        summary += f"❌ Errors: {errors}\n"
    if jobs_with_emails == 0:
        summary += "\nℹ️ No new jobs with email addresses found this time.\n"
    else:
        summary += f"\n✅ {jobs_with_emails} job(s) sent above — tap *Approve* to send application!"

    send_tg(summary)

    # Log
    try:
        log = []
        if os.path.exists(SCAN_LOG_FILE):
            with open(SCAN_LOG_FILE, "r") as f:
                log = json.load(f)
        log.append({
            "timestamp": datetime.now().isoformat(),
            "api_calls": api_calls,
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
        "api_calls": api_calls,
    }


# ─── CLI Entry Point (for PA scheduled task) ──────────────

if __name__ == "__main__":
    import sys

    print(f"🚀 Job Scanner started: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # Allow custom queries from command line
    if len(sys.argv) > 1:
        custom_queries = sys.argv[1:]
        print(f"Custom queries: {custom_queries}")
        result = run_scan(queries=custom_queries)
    else:
        result = run_scan()

    print(f"\nDone! Found: {result['found']} | With Emails: {result['with_emails']}")
