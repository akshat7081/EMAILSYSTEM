import os
import shutil

# Load .env first
if os.path.exists(".env"):
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

# Now import bot
import bot
bot.BASE_DIR = "."
bot.env_path = ".env"
bot.RESUME_FILE = "resume.pdf"
bot.LOG_FILE = "sent_log.txt"
bot.REPORT_FILE = "daily_report.txt"
bot.ERROR_LOG = "error_log.txt"
bot.LOCK_FILE = "mail_bot.lock"

# Important: bot module variables were already evaluated, so override them explicitly
bot.YOUR_EMAIL = os.environ.get("GMAIL_EMAIL", "")
bot.YOUR_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

print("Using email:", bot.YOUR_EMAIL)

if not os.path.exists("mail_queue.json"):
    shutil.copy("data/mail_queue.json", "mail_queue.json")

bot.main()
