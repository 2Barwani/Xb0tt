# X News Bot 🤖

Automatically posts news about **SPY/Stock Market**, **War/Geopolitics**, **Federal Reserve**, and **Bitcoin** to your X account.

---

## Features

| Topic | Hashtags | Post Frequency |
|-------|----------|---------------|
| 📈 SPY / Stock Market | #SPY #SP500 #StockMarket | Every 2 hours |
| 🌍 War / Geopolitics | #Geopolitics #WorldNews #War | Every 3 hours |
| 🏦 Federal Reserve | #Fed #FederalReserve #InterestRates | Every 4 hours |
| ₿ Bitcoin / Crypto | #Bitcoin #BTC #Crypto | Every 90 minutes |

- Pulls from **real RSS feeds** (BBC, NYT, MarketWatch, WSJ, CoinDesk, CoinTelegraph, Yahoo Finance)
- Filters articles by **topic-specific keywords** to ensure relevance
- **Deduplication** — never posts the same article twice
- Tweets are auto-trimmed to fit the **280 character limit**
- **Dry-run mode** to preview tweets before going live

---

## Setup

### 1. Get X API Access

1. Go to [developer.twitter.com](https://developer.twitter.com/en/portal/dashboard)
2. Create a new Project + App
3. Apply for **Elevated access** (required for posting)
4. Under your app → Keys and Tokens, generate:
   - API Key & Secret
   - Access Token & Secret (set permissions to **Read + Write**)
   - Bearer Token

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure credentials

```bash
cp .env.example .env
# Edit .env and paste your keys
```

### 4. Test with dry-run

```bash
# Preview all topics — no tweets will be posted
python bot.py --dry-run

# Preview a single topic
python bot.py --topic bitcoin --dry-run
python bot.py --topic spy --dry-run
python bot.py --topic war --dry-run
python bot.py --topic fed --dry-run
```

### 5. Go live

```bash
# Post all topics on a schedule (runs forever)
python bot.py

# Post a single topic and exit
python bot.py --topic bitcoin
```

---

## Running 24/7

### Option A — Screen (simple)
```bash
screen -S xbot
python bot.py
# Ctrl+A then D to detach
```

### Option B — systemd service (recommended for VPS/Linux)

Create `/etc/systemd/system/xbot.service`:
```ini
[Unit]
Description=X News Bot
After=network.target

[Service]
WorkingDirectory=/path/to/x_news_bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=30
User=youruser

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable xbot
sudo systemctl start xbot
sudo systemctl status xbot
```

### Option C — GitHub Actions (free cloud hosting)

Create `.github/workflows/bot.yml` to run on a cron schedule using repository secrets for credentials.

---

## Customizing

### Change post frequency
Edit the `run_scheduler()` function in `bot.py`:
```python
schedule.every(2).hours.do(post_topic, "spy", dry_run)    # Change interval
schedule.every().day.at("09:30").do(post_topic, "spy", dry_run)  # Or fixed time
```

### Add new topics
Add a new entry to the `TOPICS` dict in `bot.py`:
```python
"gold": {
    "label": "Gold / Commodities",
    "hashtags": "#Gold #XAU #Commodities",
    "emoji": "🥇",
    "feeds": ["https://..."],
    "keywords": ["gold", "xau", "commodity", "silver"],
},
```

### Add new RSS feeds
Append URLs to any topic's `feeds` list. Any valid RSS/Atom feed works.

---

## Files

```
x_news_bot/
├── bot.py           # Main bot script
├── requirements.txt # Python dependencies
├── .env.example     # Credential template
├── .env             # Your credentials (never commit this!)
├── posted.txt       # Auto-created; tracks posted URLs
└── bot.log          # Auto-created; activity log
```

---

## Notes

- X's free API tier allows **~17 tweets/day**. The default schedule posts ~18/day — adjust intervals if you hit rate limits.
- X's **Basic tier ($100/mo)** allows 100 tweets/day with no restrictions.
- The bot logs all activity to `bot.log` for debugging.
