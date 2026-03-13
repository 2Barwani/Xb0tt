"""
X News Bot — Auto-posts news about SPY, War, Fed, Bitcoin
Requires: tweepy, requests, feedparser, python-dotenv, schedule
"""

import os
import time
import random
import logging
import schedule
import feedparser
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
import tweepy

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ── Auth ──────────────────────────────────────────────────────────────────────

def get_client():
    return tweepy.Client(
        consumer_key=os.getenv("X_API_KEY"),
        consumer_secret=os.getenv("X_API_SECRET"),
        access_token=os.getenv("X_ACCESS_TOKEN"),
        access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET"),
        bearer_token=os.getenv("X_BEARER_TOKEN"),
        wait_on_rate_limit=True,
    )

# ── Topic config ──────────────────────────────────────────────────────────────

TOPICS = {
    "spy": {
        "label": "SPY / Stock Market",
        "hashtags": "#SPY #SP500 #StockMarket #Investing",
        "emoji": "📈",
        "feeds": [
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US",
            "https://www.marketwatch.com/rss/topstories",
            "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        ],
        "keywords": ["SPY", "S&P 500", "stock", "market", "equities", "nasdaq", "dow", "wall street", "rally", "selloff"],
    },
    "war": {
        "label": "War / Geopolitics",
        "hashtags": "#Geopolitics #WorldNews #War",
        "emoji": "🌍",
        "feeds": [
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
            "https://www.aljazeera.com/xml/rss/all.xml",
        ],
        "keywords": ["war", "conflict", "military", "troops", "missile", "attack", "ceasefire", "ukraine", "russia", "israel", "gaza", "nato", "sanctions", "invasion"],
        "x_accounts": ["WarMonitor3"],
    },
    "fed": {
        "label": "Federal Reserve",
        "hashtags": "#Fed #FederalReserve #InterestRates #Inflation #Economy",
        "emoji": "🏦",
        "feeds": [
            "https://feeds.a.dj.com/rss/RSSEconomy.xml",
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
            "https://www.marketwatch.com/rss/economy-politics",
        ],
        "keywords": ["fed", "federal reserve", "fomc", "powell", "interest rate", "rate hike", "rate cut", "inflation", "cpi", "monetary policy", "basis points", "treasury", "yield"],
    },
    "bitcoin": {
        "label": "Bitcoin / Crypto",
        "hashtags": "#Bitcoin #BTC #Crypto #Cryptocurrency",
        "emoji": "₿",
        "feeds": [
            "https://feeds.feedburner.com/CoinDesk",
            "https://cointelegraph.com/rss",
            "https://bitcoinmagazine.com/feed",
        ],
        "keywords": ["bitcoin", "btc", "crypto", "blockchain", "satoshi", "halving", "etf", "coinbase", "binance", "ethereum", "defi", "web3", "whale"],
    },
}

# ── X account fetching ────────────────────────────────────────────────────────

def fetch_x_account_tweets(username: str, limit: int = 10) -> list[dict]:
    """Fetch recent tweets from an X account using the API."""
    try:
        client = get_client()
        user = client.get_user(username=username)
        if not user.data:
            log.warning(f"X user not found: @{username}")
            return []
        user_id = user.data.id
        tweets = client.get_users_tweets(
            user_id,
            max_results=min(limit, 100),
            tweet_fields=["created_at", "text"],
            exclude=["retweets", "replies"],
        )
        if not tweets.data:
            return []
        articles = []
        for tweet in tweets.data:
            text = tweet.text.strip()
            if not text:
                continue
            # Use first 100 chars as title, full text as summary
            title = text[:100] + ("…" if len(text) > 100 else "")
            link = f"https://x.com/{username}/status/{tweet.id}"
            articles.append({"title": title, "link": link, "summary": text})
        return articles
    except Exception as e:
        log.warning(f"X account fetch error (@{username}): {e}")
        return []

# ── RSS fetching ──────────────────────────────────────────────────────────────

def fetch_articles(topic_key: str, limit: int = 20) -> list[dict]:
    topic = TOPICS[topic_key]
    articles = []
    for url in topic["feeds"]:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:limit]:
                title = entry.get("title", "").strip()
                link  = entry.get("link", "").strip()
                summary = entry.get("summary", "").strip()
                if not title or not link:
                    continue
                text = (title + " " + summary).lower()
                if any(kw in text for kw in topic["keywords"]):
                    articles.append({"title": title, "link": link, "summary": summary})
        except Exception as e:
            log.warning(f"Feed error ({url}): {e}")

    # Fetch from X accounts if configured
    for username in topic.get("x_accounts", []):
        x_articles = fetch_x_account_tweets(username, limit=10)
        articles.extend(x_articles)

    seen, unique = set(), []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique.append(a)
    return unique

# ── Tweet composer ────────────────────────────────────────────────────────────

MAX_TWEET = 280

def compose_tweet(topic_key: str, article: dict) -> str:
    topic = TOPICS[topic_key]
    emoji = topic["emoji"]
    tags  = topic["hashtags"]
    title = article["title"]
    link  = article["link"]

    # X counts URLs as 23 chars
    url_len = 23
    tag_len = len(tags) + 1          # +1 newline
    prefix  = f"{emoji} "
    suffix  = f"\n\n{tags}\n{link}"

    available = MAX_TWEET - len(prefix) - tag_len - url_len - 4
    if len(title) > available:
        title = title[:available - 1] + "…"

    return f"{prefix}{title}{suffix}"

# ── State tracker (avoids duplicate posts) ───────────────────────────────────

POSTED_FILE = "posted.txt"

def load_posted() -> set:
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE) as f:
        return set(line.strip() for line in f if line.strip())

def save_posted(url: str):
    with open(POSTED_FILE, "a") as f:
        f.write(url + "\n")

# ── Posting logic ─────────────────────────────────────────────────────────────

def post_topic(topic_key: str, dry_run: bool = False):
    log.info(f"Checking topic: {TOPICS[topic_key]['label']}")
    posted = load_posted()
    articles = fetch_articles(topic_key)

    for article in articles:
        if article["link"] in posted:
            continue
        tweet = compose_tweet(topic_key, article)
        log.info(f"Tweet ({len(tweet)} chars):\n{tweet}\n")
        if not dry_run:
            try:
                client = get_client()
                client.create_tweet(text=tweet)
                log.info("✓ Posted successfully")
            except tweepy.TweepyException as e:
                log.error(f"Twitter API error: {e}")
                return
        save_posted(article["link"])
        time.sleep(random.uniform(5, 15))   # polite delay
        return   # one article per call; scheduler handles frequency

    log.info(f"No new articles found for {topic_key}")

# ── Scheduler ─────────────────────────────────────────────────────────────────

def run_scheduler(dry_run: bool = False):
    log.info("X News Bot started. Scheduling jobs…")

    # SPY — every 2 hours on weekdays (market hours skew)
    schedule.every(2).hours.do(post_topic, "spy", dry_run)

    # War — every 3 hours
    schedule.every(3).hours.do(post_topic, "war", dry_run)

    # Fed — every 4 hours
    schedule.every(4).hours.do(post_topic, "fed", dry_run)

    # Bitcoin — every 90 minutes (crypto never sleeps)
    schedule.every(90).minutes.do(post_topic, "bitcoin", dry_run)

    # Initial run on startup
    for key in TOPICS:
        post_topic(key, dry_run)

    log.info("Scheduler running. Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(60)

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="X News Bot")
    parser.add_argument("--dry-run", action="store_true", help="Print tweets without posting")
    parser.add_argument("--topic", choices=list(TOPICS.keys()), help="Post a single topic and exit")
    parser.add_argument("--once", action="store_true", help="Run all topics once and exit (for CI/cron)")
    args = parser.parse_args()

    if args.topic:
        post_topic(args.topic, dry_run=args.dry_run)
    elif args.once:
        log.info("Running single pass for all topics…")
        for key in TOPICS:
            post_topic(key, dry_run=args.dry_run)
        log.info("Single pass complete.")
    else:
        run_scheduler(dry_run=args.dry_run)
