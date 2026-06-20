# Signal Board — Your Stock Screener

This is your dashboard for tracking stocks based on three things combined:
how much the price has moved this week, how fresh and positive the latest
news is, and whether analysts are turning more bullish or bearish.

**This is a research tool, not financial advice.** It doesn't predict the
future — it just organizes public information so you can scan it faster.

---

## What you're about to do (the short version)

1. Create a free account on a site called **Finnhub** → copy a code (an "API key")
2. Create a free account on a site called **Render** → paste that code in, connect this project
3. Render builds your dashboard automatically and gives you a website link
4. You bookmark that link and visit it whenever you want to check your stocks

No coding, no terminal. Just clicking buttons on websites and copy-pasting one code.
Total time: about 15–20 minutes.

---

## Step 1 — Get your free Finnhub API key

Finnhub is the company that supplies the actual stock price, news, and
analyst data your dashboard will show.

1. Go to **https://finnhub.io/register**
2. Sign up with your email (or Google account) — it's free, no credit card needed
3. Once logged in, you'll land on a **Dashboard** page
4. Look for a box labeled **"API key"** — it's a long string of letters and numbers
5. Click the little copy icon next to it, or select and copy it manually
6. Paste it somewhere temporary for now (a Notes app, anywhere) — you'll need it in Step 3

⚠️ Treat this key like a password. Don't post it publicly or share it. Anyone
with it could use your free quota.

---

## Step 2 — Put this project on GitHub

GitHub is just the place that stores your project's code so Render can find it.

1. Go to **https://github.com** and create a free account if you don't have one
2. Click the **+** icon (top right) → **New repository**
3. Name it anything, e.g. `signal-board`
4. Leave it **Public** (Render's free tier needs this) and click **Create repository**
5. On the next page, look for **"uploading an existing file"** (a link/button on the page)
6. Drag and drop **all the files and folders** I've given you (`backend/`, `frontend/`,
   `render.yaml`, this `README.md`) into that upload box
7. Scroll down, click **Commit changes**

That's it — your code now lives on GitHub.

---

## Step 3 — Deploy it on Render

Render is the free hosting service that will actually run your dashboard
and give you a live website link.

1. Go to **https://render.com** and sign up — easiest is "Sign up with GitHub"
   so the two are connected automatically
2. Once logged in, click **New +** (top right) → **Web Service**
3. Render will ask to connect to a GitHub repo — find and select the
   `signal-board` repo you just created
4. Render should auto-detect the settings from the `render.yaml` file. If it
   asks you to confirm:
   - **Build command**: `pip install -r backend/requirements.txt`
   - **Start command**: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
5. Scroll to **Environment Variables**. Click **Add Environment Variable**:
   - **Key**: `FINNHUB_API_KEY`
   - **Value**: paste the API key you copied in Step 1
6. Choose the **Free** plan
7. Click **Create Web Service**

Render will now build your app — you'll see a log scrolling by. This takes
2–5 minutes. When it says **"Live"** with a green dot, you're done.

8. At the top of the page, you'll see your live URL — something like
   `https://signal-board-screener.onrender.com`. Click it.

You should now see your dashboard, loading your starter watchlist.

---

## A note on the free plan

Render's free tier "spins down" your app if nobody visits it for a while,
and takes about 30–60 seconds to "wake up" again on your next visit. This
is normal — just wait for the page to load on the first visit of the day.

Finnhub's free tier allows a limited number of requests per minute. The
dashboard is built to cache data for 10 minutes so you shouldn't hit this
limit during normal use. If you ever see a "rate limit" message, just wait
a minute and click Refresh again.

---

## How to read your dashboard

Each stock shows a **score from 0–100**, built from three parts:

- **📊 Momentum (up to 40 points)** — Has the price moved a lot this week,
  and is trading volume backing that move up? A 10% jump on heavy volume
  scores much higher than a 10% jump on a quiet, low-volume day.
- **📰 News (up to 30 points)** — Is there a fresh headline (today or
  yesterday) and does its wording sound positive or negative?
- **📈 Sentiment (up to 30 points)** — Are Wall Street analysts'
  published ratings trending more bullish recently?

Click any stock row to expand it and see the plain-English reason behind
each of the three numbers — nothing is a hidden black box.

**Adding your own stocks:** type a ticker symbol (like `AAPL` or `TSLA`)
into the box at the top, pick a sector (or leave it as "My Tickers"), and
click **+ Add to watchlist**. It'll show up right away.

**Removing a stock:** click the ✕ next to any stock row.

---

## If something breaks

- **"FINNHUB_API_KEY not configured" error** → go back to Render → your
  service → Environment → double check the key was pasted correctly with
  no extra spaces
- **Page loads but no stocks show up** → click "Refresh now" — the first
  load can be slow while Render wakes the app up
- **A ticker shows all zeros** → that symbol might be invalid, or Finnhub's
  free tier might not yet have analyst data for that specific company

If you want to change anything else (add more sectors, change which
stocks are in the starter list, adjust the scoring), come back and tell me
what you'd like changed — I'll update the code, and you just re-upload it
to GitHub; Render redeploys automatically.
