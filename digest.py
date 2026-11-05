"""
Article digest agent: reads RSS feeds, summarizes new articles with Claude,
and sends the digest to Telegram (and optionally email).

Setup:
  pip install feedparser requests trafilatura anthropic
  export ANTHROPIC_API_KEY=...
  export TELEGRAM_BOT_TOKEN=...   # from @BotFather
  export TELEGRAM_CHAT_ID=...     # your chat id (message @userinfobot)
  # optional email:
  export SMTP_HOST=smtp.gmail.com SMTP_USER=you@gmail.com SMTP_PASS=app-password EMAIL_TO=you@gmail.com

Run daily:  0 8 * * * /usr/bin/python3 /path/digest_agent.py
"""
import json, os, smtplib, ssl
from email.message import EmailMessage
from pathlib import Path

import feedparser, requests, trafilatura
from anthropic import Anthropic

# ---- Your sources (RSS/Atom URLs) ----
FEEDS = [
    "https://hnrss.org/frontpage",
    "https://www.theverge.com/rss/index.xml",
]
MAX_PER_FEED = 3
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
SEEN_FILE = Path(__file__).with_name("seen.json")

client = Anthropic()


def load_seen():
    return set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()


def fetch_text(url, fallback):
    try:
        html = trafilatura.fetch_url(url)
        text = trafilatura.extract(html) if html else None
        return (text or fallback)[:12000]
    except Exception:
        return fallback


def summarize(title, text):
    msg = client.messages.create(
        model=MODEL,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": f"Summarize this article in 2-3 plain paragraphs. "
                       f"Lead with the key point.\n\nTitle: {title}\n\n{text}",
        }],
    )
    return msg.content[0].text.strip()


def build_digest():
    seen, items = load_seen(), []
    for feed_url in FEEDS:
        feed = feedparser.parse(feed_url)
        for e in feed.entries[:MAX_PER_FEED]:
            link = e.get("link")
            if not link or link in seen:
                continue
            text = fetch_text(link, e.get("summary", ""))
            items.append(f"*{e.get('title', 'Untitled')}*\n{summarize(e.get('title', ''), text)}\n{link}")
            seen.add(link)
    SEEN_FILE.write_text(json.dumps(sorted(seen)))
    return items


def send_telegram(text):
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    # Telegram caps messages at 4096 chars; send in chunks
    for i in range(0, len(text), 4000):
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": text[i:i + 4000], "disable_web_page_preview": True},
            timeout=30,
        ).raise_for_status()


def send_email(text):
    if not os.getenv("SMTP_HOST"):
        return
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = "Your article digest", os.environ["SMTP_USER"], os.environ["EMAIL_TO"]
    m.set_content(text)
    with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], 465, context=ssl.create_default_context()) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(m)


if __name__ == "__main__":
    items = build_digest()
    if items:
        digest = "Your digest\n\n" + "\n\n".join(items)
        send_telegram(digest)
        send_email(digest)
    else:
        print("No new articles.")