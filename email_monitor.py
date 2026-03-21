# ═══════════════════════════════════════════════════════════
# EMAIL MONITOR - IMAP INBOX CHECKER (SELF-CONTAINED)
# Checks Gmail every 30 min, classifies, alerts on Telegram
# No imports from mail.py to avoid circular dependency
# ═══════════════════════════════════════════════════════════

import os
import re
import imaplib
import email
import json
import shutil
import logging
from email.header import decode_header
from datetime import datetime, timedelta
from threading import RLock

# ─── Own logger (no import from mail.py) ───────────────────
logger = logging.getLogger("EmailMonitor")

# ─── Own lock (shared via import in mail.py) ─────────────────
data_lock = RLock()

# ─── Config ────────────────────────────────────────────────
GMAIL_EMAIL = os.environ.get("GMAIL_EMAIL", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993
BOT_VERSION = os.environ.get("BOT_VERSION", "8.0")
CHAT_ID = os.environ.get("CHAT_ID", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
INBOX_FILE = os.path.join(DATA_DIR, "inbox_items.json")
INBOX_LOG_FILE = os.path.join(DATA_DIR, "inbox_log.json")
LAST_CHECK_FILE = os.path.join(DATA_DIR, "last_inbox_check.txt")
BATCH_ALERTS_FILE = os.path.join(DATA_DIR, "batch_alerts.json")
MAIL_QUEUE_FILE = os.path.join(DATA_DIR, "mail_queue.json")
PROCESSED_IDS_FILE = os.path.join(DATA_DIR, "processed_msg_ids.json")

inbox_lock = data_lock


def set_shared_lock(lock):
    """Bug #12 fix: Allow mail.py to pass its lock for cross-threading safety"""
    global data_lock, inbox_lock
    data_lock = lock
    inbox_lock = lock


# ─── Own JSON helpers (self-contained, no import) ───────────
def safe_load_json(filepath, default=None):
    if default is None:
        default = []
    if not os.path.exists(filepath):
        return default
    with data_lock:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            bak = filepath + ".bak"
            if os.path.exists(bak):
                try:
                    with open(bak, "r", encoding="utf-8") as f:
                        return json.load(f)
                except:
                    pass
            logger.error("Corrupted JSON: {}".format(filepath))
            return default
        except Exception as e:
            logger.error("JSON load error {}: {}".format(filepath, e))
            return default


def safe_save_json(filepath, data):
    with data_lock:
        try:
            if os.path.exists(filepath):
                shutil.copy2(filepath, filepath + ".bak")
            temp = filepath + ".tmp"
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(temp, filepath)
            return True
        except Exception as e:
            logger.error("JSON save error {}: {}".format(filepath, e))
            return False

# ─── Keywords ──────────────────────────────────────────────
INTERVIEW_KEYWORDS = [
    "interview", "schedule", "call you",
    "meeting", "zoom", "google meet",
    "available", "slot", "round",
    "technical round", "hr round",
    "walk-in", "come to office",
    "face to face", "video call",
    "tomorrow", "next week",
    "please confirm", "time slot",
]

POSITIVE_KEYWORDS = [
    "interested", "shortlisted",
    "selected", "congratulations",
    "welcome", "offer", "joining",
    "onboard", "accepted",
    "pleased to inform",
    "we would like", "happy to",
]

NEGATIVE_KEYWORDS = [
    "regret", "unfortunately",
    "not shortlisted", "rejected",
    "position filled", "no vacancy",
    "cannot proceed", "not suitable",
    "better suited candidates",
    "moved forward with",
    "not able to", "unable to",
]

NEUTRAL_KEYWORDS = [
    "acknowledge", "auto-reply", "automated email",
    "out of office", "on leave", "vacation",
    "thank you for applying", "application received",
    "interest in the position", "keep your resume",
    "under review", "selection process", "hiring manager",
    "will be in touch", "if your profile matches",
]

BOUNCE_KEYWORDS = [
    "delivery failed", "undeliverable",
    "address not found", "user unknown",
    "mailbox unavailable", "550",
    "no such user", "does not exist",
    "delivery status notification",
    "mail delivery failed",
    "returned to sender",
    "permanent failure",
    "retry", "delayed", "temporary failure", "deferred"
]

SPAM_SUBJECTS = [
    "unsubscribe", "newsletter", "promotion",
    "limited offer", "congratulations you won",
    "claim your", "act now", "discount",
    "free trial", "subscription",
]


# ═══════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

# Helper functions (safe_load_json, safe_save_json, etc.) are imported from mail.py

def get_sent_emails():
    """Get all emails we've contacted (sent, sending, or failed)"""
    queue = safe_load_json(MAIL_QUEUE_FILE, [])
    return {
        item.get("email", "").lower().strip()
        for item in queue
        if item.get("status") in ("sent", "sending", "failed")
    }


def get_last_check_time():
    """Get timestamp of last inbox check"""
    if os.path.exists(LAST_CHECK_FILE):
        try:
            with open(LAST_CHECK_FILE, "r") as f:
                ts = f.read().strip()
                # Ensure naive datetime for comparison
                return datetime.fromisoformat(ts).replace(tzinfo=None)
        except:
            pass
    return (datetime.now() - timedelta(hours=1)).replace(tzinfo=None)


def save_last_check_time():
    with open(LAST_CHECK_FILE, "w") as f:
        f.write(datetime.now().isoformat())


def load_inbox_items():
    return safe_load_json(INBOX_FILE, [])


def save_inbox_items(items):
    """Use safe_save_json for atomic write."""
    safe_save_json(INBOX_FILE, items[-500:])


def load_batch_alerts():
    """Load batch alerts with guaranteed default keys"""
    defaults = {
        "auto_replies": 0,
        "bounces": 0,
        "spam_ignored": 0,
        "followups_sent": 0,
        "last_batch_time": datetime.now().isoformat(),
        "items": [],
    }
    loaded = safe_load_json(BATCH_ALERTS_FILE, {})
    if not isinstance(loaded, dict):
        loaded = {}

    # Merge: loaded values override defaults
    defaults.update(loaded)

    # Ensure items is always a list
    if not isinstance(defaults.get("items"), list):
        defaults["items"] = []

    return defaults

def save_batch_alerts(data):
    safe_save_json(BATCH_ALERTS_FILE, data)


def reset_batch_alerts():
    save_batch_alerts({
        "auto_replies": 0,
        "bounces": 0,
        "spam_ignored": 0,
        "followups_sent": 0,
        "last_batch_time": datetime.now().isoformat(),
        "items": [],
    })


def log_inbox_activity(action, details=""):
    with data_lock:
        logs = safe_load_json(INBOX_LOG_FILE, [])
        if not isinstance(logs, list):
            logs = []
        logs.append({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": details,
        })
        safe_save_json(INBOX_LOG_FILE, logs[-300:])


def decode_subject(subject):
    """Decode email subject properly"""
    if not subject:
        return ""
    decoded = decode_header(subject)
    parts = []
    for part, charset in decoded:
        if isinstance(part, bytes):
            try:
                parts.append(part.decode(charset or "utf-8", errors="replace"))
            except:
                parts.append(part.decode("utf-8", errors="replace"))
        else:
            parts.append(str(part))
    return " ".join(parts)


def get_email_body(msg):
    """Extract plain text body from email"""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                try:
                    charset = part.get_content_charset() or "utf-8"
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode(charset, errors="replace")
                        break
                except:
                    pass
    else:
        try:
            charset = msg.get_content_charset() or "utf-8"
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode(charset, errors="replace")
        except:
            pass
    return body[:2000]


def extract_sender_email(from_header):
    """Extract clean email from From header"""
    if not from_header:
        return ""
    match = re.search(r'<([^>]+)>', from_header)
    if match:
        return match.group(1).lower().strip()
    match = re.search(
        r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
        from_header
    )
    if match:
        return match.group(0).lower().strip()
    return from_header.lower().strip()


def extract_sender_name(from_header):
    """Extract name from From header"""
    if not from_header:
        return "Unknown"
    match = re.match(r'"?([^"<]+)"?\s*<', from_header)
    if match:
        return match.group(1).strip()
    return from_header.split("@")[0]


# ═══════════════════════════════════════════════════════════
# CLASSIFIER - THE BRAIN
# ═══════════════════════════════════════════════════════════

def classify_email(sender, subject, body, sent_emails):
    """
    Classify incoming email into category

    Returns dict:
    {
        "type": "interview" | "positive" | "negative" |
                "neutral" | "bounce" | "spam" | "reply" | "unknown",
        "priority": "CRITICAL" | "URGENT" | "NORMAL" | "LOW" | "IGNORE",
        "alert": "instant" | "batch" | "digest" | "none",
        "action": description of what to do,
        "matched_keywords": [...],
    }
    """
    sender_lower = sender.lower()
    subject_lower = subject.lower()
    body_lower = body.lower()
    full_text = f"{subject_lower} {body_lower}"

    result = {
        "type": "unknown",
        "priority": "LOW",
        "alert": "digest",
        "action": "log only",
        "matched_keywords": [],
    }

    # ── CHECK 1: Is it a bounce? ──────────────────────────
    for kw in BOUNCE_KEYWORDS:
        if kw in full_text:
            result["matched_keywords"].append(kw)
    if result["matched_keywords"] and (
        "mailer-daemon" in sender_lower or
        "postmaster" in sender_lower or
        "delivery" in subject_lower or
        len(result["matched_keywords"]) >= 2
    ):
        return {
            "type": "bounce",
            "priority": "IGNORE",
            "alert": "none",
            "action": "remove_from_queue",
            "matched_keywords": result["matched_keywords"],
        }
    result["matched_keywords"] = []

    # ── CHECK 2: Is it spam? ──────────────────────────────
    for kw in SPAM_SUBJECTS:
        if kw in subject_lower:
            return {
                "type": "spam",
                "priority": "IGNORE",
                "alert": "none",
                "action": "ignore",
                "matched_keywords": [kw],
            }

    # Noreply senders
    if "noreply" in sender_lower or "no-reply" in sender_lower:
        # Could be auto-reply or bounce
        for kw in NEUTRAL_KEYWORDS:
            if kw in full_text:
                return {
                    "type": "neutral",
                    "priority": "LOW",
                    "alert": "batch",
                    "action": "log_auto_reply",
                    "matched_keywords": [kw],
                }
        return {
            "type": "spam",
            "priority": "IGNORE",
            "alert": "none",
            "action": "ignore",
            "matched_keywords": ["noreply"],
        }

    # ── CHECK 3: Is sender someone we emailed? ────────────
    is_reply = sender_lower in sent_emails

    if is_reply:
        # ── CHECK 3A: Interview call? ─────────────────────
        interview_matches = []
        for kw in INTERVIEW_KEYWORDS:
            if kw in full_text:
                interview_matches.append(kw)
        if len(interview_matches) >= 1:
            return {
                "type": "interview",
                "priority": "CRITICAL",
                "alert": "instant",
                "action": "stop_followups_alert_urgent",
                "matched_keywords": interview_matches,
            }

        # ── CHECK 3B: Positive response? ──────────────────
        positive_matches = []
        for kw in POSITIVE_KEYWORDS:
            if kw in full_text:
                positive_matches.append(kw)
        if positive_matches:
            return {
                "type": "positive",
                "priority": "CRITICAL",
                "alert": "instant",
                "action": "stop_followups_alert_positive",
                "matched_keywords": positive_matches,
            }

        # ── CHECK 3C: Rejection? ──────────────────────────
        negative_matches = []
        for kw in NEGATIVE_KEYWORDS:
            if kw in full_text:
                negative_matches.append(kw)
        if negative_matches:
            return {
                "type": "negative",
                "priority": "NORMAL",
                "alert": "batch",
                "action": "mark_rejected_stop_followups",
                "matched_keywords": negative_matches,
            }

        # ── CHECK 3D: Auto-reply/acknowledgement? ─────────
        neutral_matches = []
        for kw in NEUTRAL_KEYWORDS:
            if kw in full_text:
                neutral_matches.append(kw)
        if neutral_matches:
            return {
                "type": "neutral",
                "priority": "NORMAL",
                "alert": "batch",
                "action": "check_manually_keep_followups",
                "matched_keywords": neutral_matches,
            }

        # ── CHECK 3E: Generic reply? ──────────────────────
        return {
            "type": "reply",
            "priority": "URGENT",
            "alert": "instant",
            "action": "stop_followups_alert_reply",
            "matched_keywords": ["direct_reply"],
        }

    # ── CHECK 4: Not from sent list ───────────────────────
    # Unknown sender but has job keywords
    job_keywords_found = []
    job_words = [
        "job", "hiring", "position", "opening",
        "career", "opportunity", "vacancy",
        "resume", "application",
    ]
    for kw in job_words:
        if kw in full_text:
            job_keywords_found.append(kw)

    if len(job_keywords_found) >= 2:
        return {
            "type": "unknown",
            "priority": "LOW",
            "alert": "digest",
            "action": "log_possible_job",
            "matched_keywords": job_keywords_found,
        }

    # Everything else
    return {
        "type": "spam",
        "priority": "IGNORE",
        "alert": "none",
        "action": "ignore",
        "matched_keywords": [],
    }


# ═══════════════════════════════════════════════════════════
# IMAP FETCHER
# ═══════════════════════════════════════════════════════════

def fetch_new_emails():
    """Fetch new emails with per-connection timeout (no global side effects)"""
    if not GMAIL_APP_PASSWORD:
        return []

    last_check = get_last_check_time()
    sent_emails = get_sent_emails()
    processed_ids = set(safe_load_json(PROCESSED_IDS_FILE, []))
    results = []

    # ✅ NO global timeout change — use IMAP timeout parameter instead
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT, timeout=30)
        mail.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
        mail.select("INBOX", readonly=True)

        since_date = last_check.strftime("%d-%b-%Y")
        status, messages = mail.search(None, '(SINCE {})'.format(since_date))

        if status != "OK":
            mail.logout()
            return []

        email_ids = messages[0].split()

        for eid in email_ids[-50:]:
            try:
                # ✅ Per-operation timeout via socket on the connection
                mail.socket().settimeout(15)
                status, msg_data = mail.fetch(eid, "(RFC822)")
                if status != "OK":
                    continue

                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                from_header = msg.get("From", "")
                subject = decode_subject(msg.get("Subject", ""))
                date_str = msg.get("Date", "")
                message_id = msg.get("Message-ID", "")

                sender_email = extract_sender_email(from_header)
                sender_name = extract_sender_name(from_header)
                body = get_email_body(msg)

                if sender_email == GMAIL_EMAIL.lower():
                    continue

                if message_id and message_id in processed_ids:
                    continue

                try:
                    from email.utils import parsedate_to_datetime
                    email_date = parsedate_to_datetime(date_str)
                    if email_date is None:
                        email_date = datetime.now()
                    if email_date.tzinfo is not None:
                        email_date = email_date.replace(tzinfo=None)
                except Exception:
                    email_date = datetime.now()

                if email_date < last_check:
                    continue

                classification = classify_email(
                    sender_email, subject, body, sent_emails
                )

                if classification["priority"] == "IGNORE":
                    if classification["type"] == "bounce":
                        results.append({
                            "message_id": message_id,
                            "sender_email": sender_email,
                            "sender_name": sender_name,
                            "subject": subject,
                            "body_preview": body[:200],
                            "date": email_date.isoformat(),
                            "classification": classification,
                            "handled": False,
                        })
                    continue

                results.append({
                    "message_id": message_id,
                    "sender_email": sender_email,
                    "sender_name": sender_name,
                    "subject": subject,
                    "body_preview": body[:500],
                    "date": email_date.isoformat(),
                    "classification": classification,
                    "handled": False,
                })

                if message_id:
                    processed_ids.add(message_id)

            except Exception as e:
                logger.warning(f"Email parse error: {e}")
                continue

        try:
            mail.logout()
        except:
            pass

        save_last_check_time()

    except imaplib.IMAP4.error as e:
        log_inbox_activity("imap_error", str(e))
    except Exception as e:
        log_inbox_activity("fetch_error", str(e))

    safe_save_json(PROCESSED_IDS_FILE, list(processed_ids)[-2000:])
    return results


# ═══════════════════════════════════════════════════════════
# ACTIONS - What to do after classification
# ═══════════════════════════════════════════════════════════

def stop_followups_for(sender_email):
    """Stop follow-ups for this email in mail queue"""
    with data_lock:
        queue = safe_load_json(MAIL_QUEUE_FILE, [])
        updated = False
        for item in queue:
            if item.get("email", "").lower() == sender_email.lower():
                item["response_received"] = True
                item["followup_stage"] = 99
                item["response_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                updated = True
                break
        if updated: safe_save_json(MAIL_QUEUE_FILE, queue)
    return updated


def remove_bounced_email(sender_email_or_body, body=""):
    """Find the bounced email address and remove from queue"""
    bounced_to = None
    text = f"{sender_email_or_body} {body}"
    patterns = [
        r'(?:to|recipient|address)[\s:]+([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
        r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(?:\s+(?:was|is)\s+(?:not found|invalid|unavailable))',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            bounced_to = match.group(1).lower(); break

    if not bounced_to:
        all_emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
        sent = get_sent_emails()
        for em in all_emails:
            if em.lower() in sent:
                bounced_to = em.lower(); break

    if not bounced_to: return None

    # Mark as bounced in queue
    with data_lock:
        queue = safe_load_json(MAIL_QUEUE_FILE, [])
        found = False
        for item in queue:
            if item.get("email", "").lower() == bounced_to:
                item["status"] = "bounced"
                item["bounced"] = True
                item["bounce_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                found = True
                break
        if found:
            safe_save_json(MAIL_QUEUE_FILE, queue)
    return bounced_to


def process_email_actions(classified_email):
    """Execute actions based on classification"""
    c = classified_email["classification"]
    action = c.get("action", "")
    sender = classified_email["sender_email"]

    if "stop_followups" in action:
        stop_followups_for(sender)
        log_inbox_activity(
            "stopped_followups", sender
        )

    if action == "remove_from_queue":
        bounced = remove_bounced_email(
            sender, classified_email.get("body_preview", "")
        )
        if bounced:
            log_inbox_activity("bounced_removed", bounced)

    if "rejected" in action:
        log_inbox_activity("rejection", sender)

    # Save to inbox items
    items = load_inbox_items()

    # Dedup by message_id
    existing_ids = {
        i.get("message_id") for i in items
    }
    if classified_email.get("message_id") not in existing_ids:
        items.append(classified_email)
        save_inbox_items(items)

    return classified_email


# ═══════════════════════════════════════════════════════════
# MAIN CHECK FUNCTION (called by scheduler)
# ═══════════════════════════════════════════════════════════

def check_inbox():
    """
    Main function - fetch, classify, act.
    Returns dict of what happened for Telegram alerts.
    """
    new_emails = fetch_new_emails()

    if not new_emails:
        return {
            "instant": [],
            "batch": {},
            "total_new": 0,
        }

    instant_alerts = []
    with data_lock:
        batch = load_batch_alerts()
        if not isinstance(batch, dict):
            batch = {}
            
        # Bug fix: Ensure all keys exist
        for key in ["auto_replies", "bounces", "spam_ignored", "followups_sent"]:
            if key not in batch: batch[key] = 0
        if "items" not in batch: batch["items"] = []

        for em in new_emails:
            processed = process_email_actions(em)
            c = processed["classification"]
            alert_type = c.get("alert", "none")

            if alert_type == "instant":
                instant_alerts.append(processed)

            elif alert_type == "batch":
                if c["type"] == "neutral":
                    batch["auto_replies"] = batch.get("auto_replies", 0) + 1
                # ✅ Single append with all relevant info
                batch.setdefault("items", []).append({
                    "type": c["type"],
                    "email": em["sender_email"],
                    "subject": em.get("subject", "")[:60],
                })

            elif c["type"] == "bounce":
                batch["bounces"] = batch.get("bounces", 0) + 1

            elif c["type"] == "spam":
                batch["spam_ignored"] = batch.get(
                    "spam_ignored", 0
                ) + 1

        save_batch_alerts(batch)

    log_inbox_activity(
        "inbox_check",
        f"{len(new_emails)} new, "
        f"{len(instant_alerts)} urgent"
    )

    return {
        "instant": instant_alerts,
        "batch": batch,
        "total_new": len(new_emails),
    }


def get_unhandled_items():
    """Get items user hasn't handled yet"""
    items = load_inbox_items()
    unhandled = [
        i for i in items
        if not i.get("handled") and
        i["classification"]["priority"] in (
            "CRITICAL", "URGENT", "NORMAL"
        )
    ]

    # Sort by priority
    order = {"CRITICAL": 0, "URGENT": 1, "NORMAL": 2}
    unhandled.sort(
        key=lambda x: order.get(
            x["classification"]["priority"], 3
        )
    )
    
    return unhandled


def mark_handled(index):
    """Mark inbox item as handled by index"""
    with data_lock:
        items = load_inbox_items()
        unhandled = [
            i for i in items
            if not i.get("handled") and
            i["classification"]["priority"] in (
                "CRITICAL", "URGENT", "NORMAL"
            )
        ]

        if 0 <= index < len(unhandled):
            msg_id = unhandled[index].get("message_id")
            for item in items:
                if item.get("message_id") == msg_id:
                    item["handled"] = True
                    item["handled_at"] = datetime.now().strftime(
                        "%Y-%m-%d %H:%M"
                    )
                    break
            save_inbox_items(items)
            return True
    return False


def get_inbox_stats():
    """Get counts for inbox"""
    items = load_inbox_items()
    today = datetime.now().strftime("%Y-%m-%d")

    stats = {
        "total": len(items),
        "unhandled": 0,
        "critical": 0,
        "urgent": 0,
        "today_replies": 0,
        "today_bounces": 0,
        "today_auto": 0,
        "total_responses": 0,
    }

    for item in items:
        c = item.get("classification", {})
        if not item.get("handled"):
            if c.get("priority") in ("CRITICAL", "URGENT", "NORMAL"):
                stats["unhandled"] += 1
            if c.get("priority") == "CRITICAL":
                stats["critical"] += 1
            if c.get("priority") == "URGENT":
                stats["urgent"] += 1

        item_date = item.get("date", "")
        if item_date.startswith(today):
            if c.get("type") in ("reply", "positive", "interview"):
                stats["today_replies"] += 1
            if c.get("type") == "bounce":
                stats["today_bounces"] += 1
            if c.get("type") == "neutral":
                stats["today_auto"] += 1

        if c.get("type") in ("reply", "positive", "interview"):
            stats["total_responses"] += 1

    return stats

