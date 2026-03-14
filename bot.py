"""
X News Bot — Auto-posts news about SPY, War, Fed, Bitcoin
Requires: tweepy, requests, feedparser, python-dotenv
"""

import os
import json
import time
import random
import logging
import re
import tempfile
import requests
import feedparser
from datetime import datetime, timezone, timedelta
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

def get_v1_api():
    """v1.1 API for media uploads."""
    auth = tweepy.OAuth1UserHandler(
        os.getenv("X_API_KEY"),
        os.getenv("X_API_SECRET"),
        os.getenv("X_ACCESS_TOKEN"),
        os.getenv("X_ACCESS_TOKEN_SECRET"),
    )
    return tweepy.API(auth)

# ── Topic config ──────────────────────────────────────────────────────────────
#
#  Schedule strategy (17 tweets/day max):
#    SPY  — every 90 min  → ~16 posts/day max
#    War  — instant       → every run
#    Fed  — news-only     → only when fresh news exists (no fixed interval)
#    Bitcoin — every 4 hr → ~6 posts/day max
#
#  Budget split (17/day):
#    SPY:     7 tweets/day
#    War:     5 tweets/day
#    Bitcoin: 4 tweets/day
#    Fed:     1 tweet/day  (only when newsworthy)
# ──────────────────────────────────────────────────────────────────────────────

DAILY_LIMIT = 17

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
        "cooldown_minutes": 90,
        "daily_max": 7,
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
        "x_accounts": ["osint613", "sentdefender", "WarMonitor3"],
        "cooldown_minutes": 0,       # instant — post whenever news is found
        "daily_max": 5,
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
        "cooldown_minutes": 0,       # no cooldown — but only posts when news matches
        "daily_max": 1,              # max 1/day, only when newsworthy
        "news_only": True,           # skip if no fresh keyword-matched articles
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
        "cooldown_minutes": 240,     # every 4 hours
        "daily_max": 4,
    },
}

# ── State tracking ───────────────────────────────────────────────────────────

STATE_FILE = "bot_state.json"
POSTED_FILE = "posted.txt"

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"date": "", "daily_total": 0, "topics": {}}
    with open(STATE_FILE) as f:
        return json.load(f)

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def get_state_for_today(state: dict) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("date") != today:
        # New day — reset counters
        state["date"] = today
        state["daily_total"] = 0
        state["topics"] = {}
    return state

def get_topic_state(state: dict, topic_key: str) -> dict:
    if topic_key not in state["topics"]:
        state["topics"][topic_key] = {"count": 0, "last_posted": ""}
    return state["topics"][topic_key]

def can_post_topic(state: dict, topic_key: str) -> bool:
    topic_cfg = TOPICS[topic_key]
    topic_state = get_topic_state(state, topic_key)

    # Check global daily limit
    if state["daily_total"] >= DAILY_LIMIT:
        log.info(f"Daily limit reached ({DAILY_LIMIT} tweets). Skipping all.")
        return False

    # Check per-topic daily limit
    if topic_state["count"] >= topic_cfg["daily_max"]:
        log.info(f"Daily limit for {topic_key} reached ({topic_cfg['daily_max']}). Skipping.")
        return False

    # Check cooldown
    cooldown = topic_cfg["cooldown_minutes"]
    if cooldown > 0 and topic_state["last_posted"]:
        last = datetime.fromisoformat(topic_state["last_posted"])
        elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60
        if elapsed < cooldown:
            log.info(f"Cooldown for {topic_key}: {int(cooldown - elapsed)} min remaining. Skipping.")
            return False

    return True

def record_post(state: dict, topic_key: str):
    topic_state = get_topic_state(state, topic_key)
    topic_state["count"] += 1
    topic_state["last_posted"] = datetime.now(timezone.utc).isoformat()
    state["daily_total"] += 1

# ── X account fetching ────────────────────────────────────────────────────────

def download_video(url: str) -> str | None:
    """Download video to a temp file. Returns path or None."""
    try:
        resp = requests.get(url, stream=True, timeout=30)
        resp.raise_for_status()
        suffix = ".mp4"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        for chunk in resp.iter_content(chunk_size=8192):
            tmp.write(chunk)
        tmp.close()
        log.info(f"Downloaded video: {tmp.name}")
        return tmp.name
    except Exception as e:
        log.warning(f"Video download error: {e}")
        return None


def upload_video(video_path: str) -> int | None:
    """Upload video via v1.1 chunked upload. Returns media_id or None."""
    try:
        api = get_v1_api()
        media = api.media_upload(
            video_path,
            media_category="tweet_video",
            chunked=True,
        )
        log.info(f"Uploaded video, media_id: {media.media_id}")
        return media.media_id
    except Exception as e:
        log.warning(f"Video upload error: {e}")
        return None


def clean_tweet_text(text: str) -> str:
    """Clean up tweet text: remove URLs, @mentions, hashtags, extra whitespace."""
    text = re.sub(r"https?://\S+", "", text)       # remove URLs
    text = re.sub(r"@\w+", "", text)                # remove @mentions
    text = re.sub(r"#\w+", "", text)                # remove hashtags
    text = re.sub(r"\s{2,}", " ", text).strip()     # collapse whitespace
    # Remove leading/trailing punctuation artifacts
    text = text.strip("- :;,.")
    # Make headline trendy — capitalize first letter of each sentence, add punch
    if text:
        text = ". ".join(s.strip().capitalize() for s in text.split(". ") if s.strip())
        # Add BREAKING prefix if not already there
        upper = text.upper()
        if not any(tag in upper for tag in ["BREAKING", "JUST IN", "URGENT", "DEVELOPING"]):
            text = f"BREAKING: {text}"
    return text


def fetch_x_account_tweets(username: str, limit: int = 10) -> list[dict]:
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
            tweet_fields=["created_at", "text", "attachments"],
            media_fields=["type", "variants"],
            expansions=["attachments.media_keys"],
            exclude=["retweets", "replies"],
        )
        if not tweets.data:
            return []
        # Map media keys to downloadable URLs (video, image, gif)
        media_map = {}  # key -> {"url": ..., "type": ...}
        if tweets.includes and "media" in tweets.includes:
            for media in tweets.includes["media"]:
                if media.type == "video" and hasattr(media, "variants"):
                    mp4s = [v for v in media.variants if v.get("content_type") == "video/mp4"]
                    if mp4s:
                        best = max(mp4s, key=lambda v: v.get("bit_rate", 0))
                        media_map[media.media_key] = {"url": best["url"], "type": "video"}
                elif media.type == "animated_gif" and hasattr(media, "variants"):
                    mp4s = [v for v in media.variants if v.get("content_type") == "video/mp4"]
                    if mp4s:
                        media_map[media.media_key] = {"url": mp4s[0]["url"], "type": "gif"}
                elif media.type == "photo" and hasattr(media, "url"):
                    media_map[media.media_key] = {"url": media.url, "type": "photo"}
        articles = []
        for tweet in tweets.data:
            text = clean_tweet_text(tweet.text)
            if not text:
                continue
            title = text[:100] + ("…" if len(text) > 100 else "")
            link = f"https://x.com/{username}/status/{tweet.id}"
            # Check for media (video, image, gif)
            media_info = None
            if tweet.attachments and "media_keys" in tweet.attachments:
                for key in tweet.attachments["media_keys"]:
                    if key in media_map:
                        media_info = media_map[key]
                        break
            articles.append({"title": title, "link": link, "summary": text, "media": media_info})
        return articles
    except Exception as e:
        log.warning(f"X account fetch error (@{username}): {e}")
        return []

# ── RSS fetching ──────────────────────────────────────────────────────────────

def fetch_articles(topic_key: str, limit: int = 20) -> list[dict]:
    topic = TOPICS[topic_key]
    articles = []

    # Fetch from X accounts first (priority over RSS)
    for username in topic.get("x_accounts", []):
        x_articles = fetch_x_account_tweets(username, limit=10)
        articles.extend(x_articles)

    # Fetch from RSS feeds
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
    label = topic["label"]
    tags  = topic["hashtags"]
    title = article["title"]
    link  = article["link"]

    # Skip link for X account sources (used only for dedup, not in tweet)
    is_x_source = link.startswith("https://x.com/")

    header = f"🔴 {label}\n\n"
    link_section = "" if is_x_source else f"\n\n📎 {link}"
    footer = f"\n\n{tags}"

    url_len = 0 if is_x_source else 23
    overhead = len(header) + len(footer) + (5 if not is_x_source else 0) + url_len
    available = MAX_TWEET - overhead
    if len(title) > available:
        title = title[:available - 1] + "…"

    return f"{header}{title}{link_section}{footer}"

# ── Posted tracker (avoids duplicate posts) ──────────────────────────────────

def load_posted() -> set:
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE) as f:
        return set(line.strip() for line in f if line.strip())

def save_posted(url: str):
    with open(POSTED_FILE, "a") as f:
        f.write(url + "\n")

# ── Posting logic ─────────────────────────────────────────────────────────────

def post_topic(topic_key: str, state: dict, dry_run: bool = False) -> bool:
    """Post one article for a topic. Returns True if a tweet was posted."""
    topic_cfg = TOPICS[topic_key]
    log.info(f"Checking topic: {topic_cfg['label']}")

    if not can_post_topic(state, topic_key):
        return False

    posted = load_posted()
    articles = fetch_articles(topic_key)

    # For news-only topics (Fed), skip entirely if no fresh articles
    if topic_cfg.get("news_only") and not articles:
        log.info(f"No news for {topic_key} (news-only mode). Skipping.")
        return False

    for article in articles:
        if article["link"] in posted:
            continue
        tweet = compose_tweet(topic_key, article)
        log.info(f"Tweet ({len(tweet)} chars):\n{tweet}\n")
        if not dry_run:
            try:
                client = get_client()
                media_id = None
                media_info = article.get("media")
                if media_info:
                    file_path = download_video(media_info["url"])
                    if file_path:
                        if media_info["type"] in ("video", "gif"):
                            media_id = upload_video(file_path)
                        else:
                            # Photo upload via v1.1
                            try:
                                api = get_v1_api()
                                uploaded = api.media_upload(file_path)
                                media_id = uploaded.media_id
                            except Exception as e:
                                log.warning(f"Photo upload error: {e}")
                        os.unlink(file_path)
                if media_id:
                    client.create_tweet(text=tweet, media_ids=[media_id])
                    log.info(f"Posted successfully (with {media_info['type']})")
                else:
                    client.create_tweet(text=tweet)
                    log.info("Posted successfully")
            except tweepy.TweepyException as e:
                log.error(f"Twitter API error: {e}")
                return False
        save_posted(article["link"])
        record_post(state, topic_key)
        log.info(f"Daily total: {state['daily_total']}/{DAILY_LIMIT}")
        return True

    log.info(f"No new articles for {topic_key}")
    return False

# ── Single pass (for GitHub Actions cron) ────────────────────────────────────

def run_once(dry_run: bool = False):
    """Run one pass: check all topics respecting cooldowns and daily limit."""
    state = load_state()
    state = get_state_for_today(state)

    log.info(f"=== Bot run at {datetime.now(timezone.utc).isoformat()} ===")
    log.info(f"Daily tweets so far: {state['daily_total']}/{DAILY_LIMIT}")

    if state["daily_total"] >= DAILY_LIMIT:
        log.info("Daily limit already reached. Nothing to do.")
        save_state(state)
        return

    # Priority: War (X accounts) + SPY + Fed first, then Bitcoin
    for topic_key in ["war", "spy", "fed", "bitcoin"]:
        if state["daily_total"] >= DAILY_LIMIT:
            log.info("Daily limit reached mid-run. Stopping.")
            break
        post_topic(topic_key, state, dry_run)
        time.sleep(random.uniform(2, 5))

    log.info(f"=== Run complete. Daily total: {state['daily_total']}/{DAILY_LIMIT} ===")
    save_state(state)

# ── Scheduler (for local use) ────────────────────────────────────────────────

def run_scheduler(dry_run: bool = False):
    import schedule as sched
    log.info("X News Bot started (local scheduler mode).")

    sched.every(90).minutes.do(run_once, dry_run)

    # Initial run
    run_once(dry_run)

    log.info("Scheduler running. Press Ctrl+C to stop.")
    while True:
        sched.run_pending()
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
        state = load_state()
        state = get_state_for_today(state)
        post_topic(args.topic, state, dry_run=args.dry_run)
        save_state(state)
    elif args.once:
        run_once(dry_run=args.dry_run)
    else:
        run_scheduler(dry_run=args.dry_run)
