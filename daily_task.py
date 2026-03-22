# ============================================================
# DAILY TASK v1.0 — ALL-IN-ONE PA SCHEDULED RUNNER
# Runs automatically via PA scheduled task every day
# ============================================================
# Phase 1: 🔍 Job Scanner — find new jobs with HR emails
# Phase 2: 📧 Email Sender — send queued emails
# Phase 3: 📩 Follow-Up — auto follow-up after 4 days
# Phase 4: 📊 Daily Summary — full report to Telegram
# ============================================================

import os, sys, json, time, logging
from datetime import datetime

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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(BASE_DIR, "daily_task.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("DailyTask")


def send_telegram(msg):
    """Quick TG notification."""
    import urllib.request, urllib.parse
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": msg[:4000],
            "parse_mode": "Markdown"
        }).encode()
        urllib.request.urlopen(url, data, timeout=10)
    except:
        pass


def run_scanner():
    """Phase 1: Run job scanner to find new jobs with emails."""
    logger.info("=" * 50)
    logger.info("PHASE 1: JOB SCANNER")
    logger.info("=" * 50)

    scanner_path = os.path.join(BASE_DIR, "job_scanner.py")
    if not os.path.exists(scanner_path):
        logger.warning("job_scanner.py not found, skipping scan phase")
        return {"found": 0, "with_emails": 0, "errors": 0}

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("job_scanner", scanner_path)
        scanner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scanner)

        result = scanner.run_scan(notify_start=True)
        logger.info(f"Scanner done: {result}")
        return result
    except Exception as e:
        logger.error(f"Scanner failed: {e}")
        send_telegram(f"❌ *Scanner Error:*\n`{str(e)[:200]}`")
        return {"found": 0, "with_emails": 0, "errors": 1}


def run_mailer():
    """Phase 2 + 3: Run email sender + follow-ups."""
    logger.info("=" * 50)
    logger.info("PHASE 2+3: EMAIL SENDER + FOLLOW-UPS")
    logger.info("=" * 50)

    bot_path = os.path.join(BASE_DIR, "bot.py")
    if not os.path.exists(bot_path):
        logger.warning("bot.py not found, skipping mailer phase")
        return

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("bot", bot_path)
        bot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bot)

        bot.main()
        logger.info("Mailer done!")
    except Exception as e:
        logger.error(f"Mailer failed: {e}")
        send_telegram(f"❌ *Mailer Error:*\n`{str(e)[:200]}`")


def main():
    start_time = datetime.now()
    logger.info(f"🚀 Daily Task started: {start_time.strftime('%Y-%m-%d %H:%M')}")

    send_telegram(
        f"⚡ *Daily Task Started*\n"
        f"📅 {start_time.strftime('%Y-%m-%d %H:%M')} IST\n\n"
        f"Phase 1: 🔍 Scanning for new jobs...\n"
        f"Phase 2: 📧 Sending queued emails...\n"
        f"Phase 3: 📩 Auto follow-ups...\n"
        f"Phase 4: 📊 Daily summary..."
    )

    # ── Phase 1: Job Scanner ──
    scan_result = run_scanner()

    # ── Small gap before sending emails ──
    time.sleep(10)

    # ── Phase 2+3: Email Sender + Follow-ups ──
    run_mailer()

    # ── Phase 4: Summary ──
    elapsed = (datetime.now() - start_time).total_seconds()
    elapsed_min = int(elapsed // 60)

    logger.info(f"✅ Daily Task complete in {elapsed_min} minutes")

    # Final summary
    summary = (
        f"📊 *Daily Task Complete!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔍 *Scanner:*\n"
        f"  Jobs found: {scan_result.get('found', 0)}\n"
        f"  With HR emails: *{scan_result.get('with_emails', 0)}*\n\n"
        f"⏱ Total time: {elapsed_min} min\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')} IST\n\n"
        f"_Next run: tomorrow same time_"
    )
    send_telegram(summary)


if __name__ == "__main__":
    main()
