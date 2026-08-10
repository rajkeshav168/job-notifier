"""
notifier.py — Send alerts when new jobs are found.

DESIGN:
- Multiple notification channels, all independent
- Each channel can be enabled/disabled in config
- All channels get the same normalized job data
- Easy to add new channels (Telegram, Discord, etc.) — just add a function

HOW EMAIL WORKS (for learning):
- SMTP = Simple Mail Transfer Protocol — the standard for sending email
- Gmail requires an "App Password" (not your real password) since 2022
- smtplib is Python's built-in SMTP client — no pip install needed
- We use STARTTLS (port 587) which upgrades a plain connection to encrypted

HOW SLACK WEBHOOKS WORK:
- Create an "Incoming Webhook" in your Slack workspace settings
- You get a URL like https://hooks.slack.com/services/T.../B.../xxx
- POST a JSON payload to it → message appears in your channel
- No SDK needed, just a single HTTP POST
"""

import smtplib
import logging
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def notify_console(jobs: list[dict], **kwargs):
    """Print new jobs to stdout. Always useful for debugging/testing."""
    print(f"\n{'='*60}")
    print(f"  🔔 {len(jobs)} NEW JOB(S) FOUND — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    for job in jobs:
        print(f"\n  📌 {job['title']}")
        print(f"     Company:  {job['company']}")
        print(f"     Location: {job['location']}")
        print(f"     Link:     {job['url']}")

    print(f"\n{'='*60}\n")


def notify_email(jobs: list[dict], config: dict):
    """
    Send an HTML email with the new job listings.

    config keys: smtp_host, smtp_port, username, password, from, to (list)
    """
    if not jobs:
        return

    # Build HTML body
    rows = ""
    for job in jobs:
        rows += f"""
        <tr>
            <td style="padding:8px; border-bottom:1px solid #eee;">
                <a href="{job['url']}" style="color:#1a73e8; text-decoration:none; font-weight:bold;">
                    {job['title']}
                </a>
            </td>
            <td style="padding:8px; border-bottom:1px solid #eee;">{job['company']}</td>
            <td style="padding:8px; border-bottom:1px solid #eee;">{job['location']}</td>
        </tr>
        """

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
        <h2 style="color:#333;">🔔 {len(jobs)} New Job Opening(s)</h2>
        <table style="border-collapse: collapse; width: 100%;">
            <thead>
                <tr style="background: #f5f5f5;">
                    <th style="padding:8px; text-align:left;">Role</th>
                    <th style="padding:8px; text-align:left;">Company</th>
                    <th style="padding:8px; text-align:left;">Location</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
        <p style="color:#888; font-size:12px; margin-top:20px;">
            Sent by Job Notifier at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
        </p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🔔 {len(jobs)} new job opening(s) matched your filters"
    msg["From"] = config["from"]
    msg["To"] = ", ".join(config["to"])
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(config["smtp_host"], config["smtp_port"]) as server:
            server.starttls()               # upgrade to encrypted
            server.login(config["username"], config["password"])
            server.sendmail(config["from"], config["to"], msg.as_string())
        logger.info(f"[Email] Sent alert for {len(jobs)} jobs to {config['to']}")
    except Exception as e:
        logger.error(f"[Email] Failed to send: {e}")


def notify_slack(jobs: list[dict], config: dict):
    """
    Send a Slack message via webhook.

    config keys: webhook_url
    """
    if not jobs:
        return

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔔 {len(jobs)} New Job Opening(s)"}
        }
    ]

    for job in jobs:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*<{job['url']}|{job['title']}>*\n{job['company']} · {job['location']}"
            }
        })

    payload = {"blocks": blocks}

    try:
        resp = requests.post(config["webhook_url"], json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"[Slack] Sent alert for {len(jobs)} jobs")
    except Exception as e:
        logger.error(f"[Slack] Failed to send: {e}")


def send_notifications(jobs: list[dict], notification_config: dict):
    """
    Dispatch to all enabled notification channels.
    This is the only function the orchestrator calls.
    """
    if not jobs:
        logger.info("[Notifier] No new jobs to notify about")
        return

    channels = notification_config

    if channels.get("console", {}).get("enabled", True):
        notify_console(jobs)

    if channels.get("email", {}).get("enabled", False):
        notify_email(jobs, channels["email"])

    if channels.get("slack", {}).get("enabled", False):
        notify_slack(jobs, channels["slack"])
