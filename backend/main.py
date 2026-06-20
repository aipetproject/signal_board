"""
Stock Screener Backend
-----------------------
Fetches price/volume and news data from Finnhub, computes a composite
"momentum + news + sentiment" score per ticker, and serves a simple
dashboard + JSON API.

IMPORTANT DATA NOTE: Finnhub's free tier does not include the
/stock/candle (historical OHLCV) endpoint. To still measure short-term
momentum without that endpoint, this app builds its own price history by
recording each ticker's quote every time the dashboard is refreshed, and
storing the last ~7 days of snapshots in a small local file. Momentum is
then computed from that self-collected history. This means: the longer
you let the app run and refresh, the more accurate the "weekly" momentum
reading becomes. On a brand new deployment, there will only be a few data
points until a week of refreshes has accumulated -- this is expected, not
a bug, and is explained on the dashboard itself.

Plain-language scoring logic (shown to the user on the dashboard too):
  - MOMENTUM (0-40 pts): how much the price has moved since the earliest
    snapshot we have on record (up to 7 days back), weighted by today's
    trading range as a proxy for conviction (since free-tier volume-over-
    time isn't available either)
  - NEWS (0-30 pts): how recent and how positive/negative the latest news is
  - SENTIMENT (0-30 pts): direction of analyst recommendation trend
    (more analysts turning bullish recently = higher score)

Total score: 0-100. Higher = more "hot" by this combined definition.
This is a research/screening tool, NOT financial advice and NOT a buy signal.
"""

import os
import time
import json
import statistics
from datetime import datetime, timedelta
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from cachetools import TTLCache

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"

# Where we persist our self-collected price history between restarts.
# Render's free tier has an ephemeral filesystem (it resets on redeploy/
# restart), so this history rebuilds over time rather than living forever --
# that's an accepted tradeoff for staying on free hosting + free data.
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "price_history.json")
HISTORY_MAX_AGE_DAYS = 8

# Starter watchlist: 3 sectors, liquid large/mid-cap names,
# chosen as "AI-automation beneficiaries not yet hyped as AI stocks"
STARTER_TICKERS = {
    "Insurance & Back-Office Automation": ["VRSK", "EFX", "ADP"],
    "Freight, Logistics & Supply Chain": ["CHRW", "UNP", "FDX"],
    "Industrial Automation Suppliers": ["ROK", "HON", "EMR"],
}

# In-memory cache so we don't hammer the free-tier rate limit.
# Cache for 10 minutes per data type.
cache = TTLCache(maxsize=500, ttl=600)

app = FastAPI(title="AI-Automation Stock Screener")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Data models ----------

class AddTickerRequest(BaseModel):
    ticker: str
    sector: Optional[str] = "My Tickers"


# ---------- Helpers: Finnhub calls (cached, rate-limit-friendly) ----------

def _cached_get(url: str, params: dict, cache_key: str):
    if cache_key in cache:
        return cache[cache_key]
    params = {**params, "token": FINNHUB_API_KEY}
    resp = requests.get(url, params=params, timeout=10)
    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail="Rate limit hit, try again shortly")
    resp.raise_for_status()
    data = resp.json()
    cache[cache_key] = data
    return data


def get_quote(ticker: str):
    """Current price, daily change, etc."""
    return _cached_get(f"{FINNHUB_BASE}/quote", {"symbol": ticker}, f"quote:{ticker}")


def get_company_news(ticker: str, days: int = 7):
    """Recent news headlines for the ticker."""
    end = datetime.now()
    start = end - timedelta(days=days)
    return _cached_get(
        f"{FINNHUB_BASE}/company-news",
        {
            "symbol": ticker,
            "from": start.strftime("%Y-%m-%d"),
            "to": end.strftime("%Y-%m-%d"),
        },
        f"news:{ticker}:{days}",
    )


def get_recommendation_trends(ticker: str):
    """Analyst buy/hold/sell trend over recent months."""
    return _cached_get(
        f"{FINNHUB_BASE}/stock/recommendation",
        {"symbol": ticker},
        f"rec:{ticker}",
    )


# ---------- Self-collected price history (works around the missing free candle endpoint) ----------

def _load_history() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_history(history: dict):
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f)
    except Exception:
        pass  # if disk write fails, we just lose this snapshot, not fatal


def record_snapshot(ticker: str, price: float, day_high: float, day_low: float):
    """Records today's price as a data point for this ticker's history.
    Only adds one snapshot per calendar day per ticker (no point storing
    every 15-minute refresh -- we care about day-to-day movement)."""
    history = _load_history()
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    ticker_history = history.get(ticker, [])

    # Don't duplicate today's entry if we already recorded one
    if ticker_history and ticker_history[-1].get("date") == today_str:
        ticker_history[-1] = {"date": today_str, "price": price, "high": day_high, "low": day_low}
    else:
        ticker_history.append({"date": today_str, "price": price, "high": day_high, "low": day_low})

    # Trim anything older than our max window
    cutoff = (datetime.utcnow() - timedelta(days=HISTORY_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    ticker_history = [pt for pt in ticker_history if pt["date"] >= cutoff]

    history[ticker] = ticker_history
    _save_history(history)
    return ticker_history


# ---------- Scoring logic ----------

def score_momentum(ticker: str, quote: dict) -> dict:
    """
    0-40 points, built WITHOUT the /stock/candle endpoint (not available on
    Finnhub's free tier). Instead, every time this app is refreshed, it
    records today's quote into a small local history file. Momentum is then
    computed by comparing today's price to the EARLIEST snapshot we have on
    record (up to 7 days back).

    Two components:
      - Price-change-since-earliest-snapshot (scaled to +/-25 points)
      - Today's trading range (high-low) as a rough proxy for "conviction",
        since free-tier doesn't expose historical volume either (0-15 points)

    On a freshly deployed app, there may only be 1-2 days of history so far --
    in that case we say so plainly rather than pretending it's a full week.
    """
    price = quote.get("c")
    day_high = quote.get("h")
    day_low = quote.get("l")
    prev_close = quote.get("pc")

    if not price or not prev_close:
        return {"points": 0, "explain": "No current price data available from Finnhub."}

    history = record_snapshot(ticker, price, day_high, day_low)

    if len(history) < 2:
        return {
            "points": 0,
            "explain": (
                "Just started tracking this stock -- need a few days of "
                "refreshes before a momentum trend can be shown."
            ),
        }

    earliest = history[0]
    days_span = (
        datetime.strptime(history[-1]["date"], "%Y-%m-%d")
        - datetime.strptime(earliest["date"], "%Y-%m-%d")
    ).days
    pct_change = (price - earliest["price"]) / earliest["price"] * 100

    # Price-change component: scale +/-10% move to +/-25 points, capped
    price_pts = max(min(pct_change, 10), -10) / 10 * 25

    # Today's range as a rough "how active is this stock today" proxy
    range_pct = ((day_high - day_low) / price * 100) if (day_high and day_low and price) else 0
    range_pts = max(min(range_pct, 5), 0) / 5 * 15  # a >=5% daily range maxes this out

    points = round(price_pts + range_pts, 1)
    points = max(min(points, 40), -25)

    direction = "up" if pct_change >= 0 else "down"
    span_note = f"over the last {days_span} day(s) we've tracked" if days_span > 0 else "today"
    explain = (
        f"Price {direction} {abs(pct_change):.1f}% {span_note}; "
        f"today's trading range is {range_pct:.1f}% of price."
    )
    if days_span < 5:
        explain += f" (Still building up history -- only {days_span} day(s) so far, not a full week yet.)"

    return {"points": points, "explain": explain, "pct_change": round(pct_change, 1)}


def score_news(news_items: list) -> dict:
    """
    0-30 points. Looks at:
      - recency of the most recent headline (today/yesterday scores higher)
      - simple keyword-based tone (positive/negative words in headline)
    This is a lightweight heuristic, not a full NLP sentiment model.
    """
    if not news_items:
        return {"points": 0, "explain": "No recent news found in the last 7 days.", "headline": None}

    positive_words = [
        "beat", "beats", "upgrade", "upgraded", "surge", "soar", "record",
        "growth", "win", "wins", "contract", "raises", "raised", "strong",
        "outperform", "buy rating", "rally",
    ]
    negative_words = [
        "miss", "misses", "downgrade", "downgraded", "plunge", "falls",
        "fall", "weak", "lawsuit", "investigation", "cuts", "cut",
        "underperform", "sell rating", "warns", "warning", "recall",
    ]

    most_recent = max(news_items, key=lambda n: n.get("datetime", 0))
    headline = most_recent.get("headline", "")
    hours_old = (time.time() - most_recent.get("datetime", time.time())) / 3600

    # Recency points: 0-18, decaying over 7 days (168 hours)
    recency_pts = max(0, 18 * (1 - hours_old / 168))

    headline_lower = headline.lower()
    tone_pts = 0
    if any(w in headline_lower for w in positive_words):
        tone_pts = 12
    elif any(w in headline_lower for w in negative_words):
        tone_pts = -12

    points = round(recency_pts + tone_pts, 1)
    points = max(min(points, 30), -12)

    age_desc = "today" if hours_old < 24 else f"{int(hours_old // 24)} day(s) ago"
    tone_desc = (
        "positive-sounding" if tone_pts > 0 else "negative-sounding" if tone_pts < 0 else "neutral"
    )
    explain = f"Latest headline ({age_desc}, {tone_desc}): \"{headline[:90]}\""

    return {"points": points, "explain": explain, "headline": headline}


def score_sentiment(rec_trends: list) -> dict:
    """
    0-30 points. Looks at analyst recommendation trend direction:
    comparing the most recent month's buy/hold/sell mix to the prior month.
    More analysts shifting toward "buy" recently = higher score.
    """
    if not rec_trends or len(rec_trends) < 2:
        return {"points": 0, "explain": "Not enough analyst data to gauge sentiment trend."}

    # Finnhub returns most recent month first
    latest = rec_trends[0]
    prior = rec_trends[1]

    def bullish_ratio(month):
        total = (month.get("strongBuy", 0) + month.get("buy", 0)
                  + month.get("hold", 0) + month.get("sell", 0)
                  + month.get("strongSell", 0))
        if total == 0:
            return 0.5
        bullish = month.get("strongBuy", 0) + month.get("buy", 0)
        return bullish / total

    latest_ratio = bullish_ratio(latest)
    prior_ratio = bullish_ratio(prior)
    shift = latest_ratio - prior_ratio  # -1 to +1

    # Base score from absolute bullishness level (0-15), plus
    # bonus/penalty for the direction of recent change (0-15)
    base_pts = latest_ratio * 15
    shift_pts = max(min(shift * 30, 15), -15)

    points = round(base_pts + shift_pts, 1)
    points = max(min(points, 30), 0)

    direction = "improving" if shift > 0.03 else "declining" if shift < -0.03 else "stable"
    explain = (
        f"{int(latest_ratio*100)}% of analysts currently bullish, trend is {direction} "
        f"vs. the prior month."
    )

    return {"points": points, "explain": explain}


def compute_full_score(ticker: str) -> dict:
    try:
        quote = get_quote(ticker)
    except Exception:
        quote = {}
    try:
        news_items = get_company_news(ticker)
    except Exception:
        news_items = []
    try:
        rec_trends = get_recommendation_trends(ticker)
    except Exception:
        rec_trends = []

    momentum = score_momentum(ticker, quote)
    news = score_news(news_items)
    sentiment = score_sentiment(rec_trends)

    total = round(momentum["points"] + news["points"] + sentiment["points"], 1)
    total = max(min(total, 100), 0)

    return {
        "ticker": ticker,
        "total_score": total,
        "current_price": quote.get("c"),
        "day_change_pct": quote.get("dp"),
        "momentum": momentum,
        "news": news,
        "sentiment": sentiment,
        "last_updated": datetime.utcnow().isoformat() + "Z",
    }


# ---------- API routes ----------

@app.get("/api/screen")
def screen_all():
    """Returns scored results for the full starter list, grouped by sector."""
    if not FINNHUB_API_KEY:
        raise HTTPException(status_code=500, detail="FINNHUB_API_KEY not configured on the server.")

    results = {}
    for sector, tickers in STARTER_TICKERS.items():
        sector_results = []
        for t in tickers:
            try:
                sector_results.append(compute_full_score(t))
            except Exception as e:
                sector_results.append({"ticker": t, "error": str(e), "total_score": 0})
        sector_results.sort(key=lambda r: r.get("total_score", 0), reverse=True)
        results[sector] = sector_results

    return results


@app.get("/api/ticker/{ticker}")
def screen_one(ticker: str):
    if not FINNHUB_API_KEY:
        raise HTTPException(status_code=500, detail="FINNHUB_API_KEY not configured on the server.")
    ticker = ticker.upper().strip()
    return compute_full_score(ticker)


@app.post("/api/add-ticker")
def add_ticker(req: AddTickerRequest):
    """Adds a custom ticker under a 'My Tickers' (or specified) sector for this session."""
    ticker = req.ticker.upper().strip()
    sector = req.sector or "My Tickers"
    if sector not in STARTER_TICKERS:
        STARTER_TICKERS[sector] = []
    if ticker not in STARTER_TICKERS[sector]:
        STARTER_TICKERS[sector].append(ticker)
    return {"status": "added", "ticker": ticker, "sector": sector}


@app.get("/api/watchlist")
def get_watchlist():
    return STARTER_TICKERS


@app.delete("/api/ticker/{sector}/{ticker}")
def remove_ticker(sector: str, ticker: str):
    ticker = ticker.upper().strip()
    if sector in STARTER_TICKERS and ticker in STARTER_TICKERS[sector]:
        STARTER_TICKERS[sector].remove(ticker)
        return {"status": "removed"}
    raise HTTPException(status_code=404, detail="Ticker/sector not found")


# ---------- Serve the frontend ----------

frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


@app.get("/")
def serve_index():
    return FileResponse(os.path.join(frontend_dir, "index.html"))


@app.get("/health")
def health():
    return {"status": "ok", "api_key_configured": bool(FINNHUB_API_KEY)}
