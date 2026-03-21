# BCA Job Hunter & Instant Mail Bot

A powerful Telegram bot built with Python, asynchronous job scheduling, OCR image extraction, and direct email delivery. Designed to run 24/7 on Replit.

## Features
- **Instant Mail with Resume:** Generates dynamic, professional email templates based on scraped job data and automatically attaches a PDF resume via PythonAnywhere scripts.
- **Image OCR:** Forward job screenshots to the bot; it instantly extracts the text and finds embedded emails using Tesseract/OCR.Space.
- **Robust Job Scraping:** Scrapes major platforms periodically with random delays and robust error handling.
- **Flask Web Dashboard:** A dark-themed web dashboard for easy control and monitoring directly via Replit's Webview.

## Setup
1. Define `TELEGRAM_BOT_TOKEN`, `CHAT_ID`, `GMAIL_EMAIL`, `GMAIL_APP_PASSWORD` in the `.env` (or Replit Secrets).
2. Run `python mail.py` to start the bot and web server simultaneously.

## Deployment
Configured out-of-the-box for Replit deployment on `python311` with strict zero-pyflakes clean code.
