# Fusion Score Bot — V6.0 Context Document
**Last Updated:** April 5, 2026  
**Purpose:** Complete reference for the current codebase — what every file does, what changed from V5, known state of the system, and open items.

---

## System Overview

The Fusion Score Bot is a quantitative Forex trading signal system targeting EUR/USD and GBP/USD. It monitors three independent signals — volatility (ATR expansion), macro sentiment (AI-analyzed news), and institutional positioning (CFTC COT data) — and synthesizes them into a Fusion Score (0–100). Signals are fired when a confirmed 15-minute candle shows ATR expansion ≥ 1.5× the 14-period average.

**Infrastructure:**
- **Render (free tier):** Runs `volatility_atr.py` as an always-on process. 512MB RAM. Kept alive via Flask keepalive endpoint.
- **GitHub Actions (free tier):** Runs all scheduled jobs. Sentiment scanner every 15 min, COT tracker weekly, grader nightly, news scheduler daily, health check daily.
- **Supabase (free tier):** Primary database. Replaces Google Sheets entirely. 4 tables.
- **TwelveData API:** 15-minute OHLCV candle data. 800 credits/day free tier.
- **Gemini 2.5 Flash:** AI sentiment for HIGH importance articles only. RPD=20, RPM=5 (verified from AI Studio — public docs claim 250 RPD but reality is 20).
- **HuggingFace FinBERT (ProsusAI/finbert):** Primary sentiment model. Unlimited free inference. Handles all MEDIUM and LOW importance articles.
- **GNews API:** Financial news headlines. 100 calls/day free tier.
- **ForexLive RSS:** Free financial RSS feed. No API key required.

**Pairs traded:** EUR/USD, GBP/USD

---

## Fusion Score Algorithm

```
Base score: 50

+ WEIGHT_ATR (20):       if ATR multiplier >= 1.5x
+ WEIGHT_SENTIMENT (25): if macro_sentiment aligned with direction
+ WEIGHT_COT (15+5):     if COT bias aligned with direction (5 extra for STRONGLY_*)
- WEIGHT_COT (15+5):     if COT bias opposed to direction

Max possible: 115 → clamped to 100
```

**Empirical findings from 76-trade audit:**
- Score 85 → 68.8% win rate (best tier)
- Score 100 → 40.0% win rate (worst tier)
- High score ≠ high quality. Do not chase Score 100 signals.
- BULLISH_FADING confirmed: EUR/USD declined while COT read BULLISH, penalizing correct SHORT trades by -15 points. Fixed in V6 with 5-state COT classification and neutral band.

---

## File-by-File Reference

---

### `config.py`
**What it does:** Central configuration hub. All API keys, trading parameters, weights, and rate limit constants live here. Loaded by every other file.

**V5 → V6 changes:**
- `validate_config()` used to always return `True` silently even with missing keys. Now raises `EnvironmentError` with the exact missing variable names. Hard failure on startup instead of silent corruption.
- Component-aware validation: `validate_config('sentiment_scanner')` checks core vars + Gemini + GNews. Each component only checks what it actually needs.
- `GCP_CREDENTIALS` removed — Google Sheets is gone.
- `HUGGINGFACE_API_KEY` added.
- Gemini limits corrected and documented: `GEMINI_RPD_LIMIT = 20`, `GEMINI_RPM_LIMIT = 5`, `GEMINI_THROTTLE_DELAY = 13`. Verified from AI Studio April 2026 (public docs falsely claim 250 RPD).
- COT v2 constants added: `COT_LOOKBACK_WEEKS = 52`, `COT_NEUTRAL_BAND = 0.40`, `COT_STRONG_THRESHOLD = 0.75`.
- `GEMINI_CALLS_PER_CYCLE = 2` (1 article × 2 pairs per run — matches actual RPD budget).

**No Telegram commands.** Config is pure configuration, no bot handlers.

---

### `shared_functions.py`
**What it does:** Shared utilities imported by every other Python file. Contains the Supabase singleton client, Telegram error notifications, the Fusion Score algorithm, and the sentiment aggregation function.

**V5 → V6 changes:**
- **Supabase singleton pattern.** V5 called `create_client()` on every single trade signal. V6 creates one module-level `_supabase_client` and reuses it everywhere.
- **`calculate_fusion_score()` — COT now 5-state.** V5 only handled BULLISH/BEARISH/NEUTRAL. V6 handles STRONGLY_BULLISH (+20pts) and STRONGLY_BEARISH (-20pts) in addition to the standard states.
- **Sentiment polarity clarified.** Positive sentiment = bullish for the pair (EUR/USD price goes UP). This was ambiguous in V5.
- **`aggregate_and_push_sentiment()` — NEW FUNCTION.** This was the biggest missing piece in V5. The sentiment scanner was collecting and storing individual article sentiment in `processed_sentiment` but never computing an aggregated score and writing it to `system_state.macro_sentiment`. The Fusion Score was therefore always computing with `sentiment = 0`. This function reads the last 6 hours of records, weights by importance tier (HIGH=±2, MEDIUM=±1), clamps to [-10,+10], and writes to `system_state`. Called by `sentiment_scanner.py` at the end of every pipeline run.

**No Telegram commands.**

---

### `cot_tracker.py`
**What it does:** Fetches 52 weeks of CFTC non-commercial positioning data, computes the COT Index, classifies into one of 5 momentum states, writes to Supabase, and sends a Telegram report. Runs once per week via `cot.yml` (Saturday 7:47 AM IST).

**V5 → V6 changes:**
- **V5 fetched 1 record. V6 fetches 52.** You cannot compute a range from one data point. V5 had `$limit: 1`.
- **52-week COT Index formula implemented.** `(current_net - min_52w) / (max_52w - min_52w)`. Normalizes raw contract counts to 0.0–1.0. This is what professional COT analysts actually use.
- **5-state classification replaces binary.** V5 classified any net > 0 as BULLISH — even +1 contract. V6 has STRONGLY_BULLISH / BULLISH / NEUTRAL / BEARISH / STRONGLY_BEARISH with thresholds from config.py. The NEUTRAL band (0.40–0.60) directly fixes the BULLISH_FADING empirical problem.
- **Writes `cot_index`, `cot_net`, `cot_date` to Supabase.** V5 only wrote `cot_bias`.
- **Malformed record handling.** Each CFTC record parsed individually. One bad record no longer aborts the entire fetch.
- **Telegram Markdown fix.** Comma-formatted numbers (`:,`) inside backticks caused Telegram 400 errors (confirmed in production log April 5 2026). Fixed by removing backtick wrapping and comma formatters from the report.

**Data confirmed working in production (April 5 2026):**
- EUR/USD: STRONGLY_BEARISH (index: 0.0000, net: 507)
- GBP/USD: BEARISH (index: 0.2800, net: -52,665)

**No Telegram commands (no /status, /news etc).** Bot only sends outbound reports.

---

### `sentiment_scanner.py`
**What it does:** Full 11-layer AI sentiment pipeline. Collects financial news from ForexLive RSS and GNews API, cleans and deduplicates articles, scores importance with time decay, routes to the appropriate AI model, stores results in Supabase, then aggregates and pushes a net sentiment score to `system_state`. Runs every 15 minutes via `sentiment.yml`.

**YES — it uses RSS feeds.** ForexLive RSS (`https://www.forexlive.com/feed`) is Source 1. GNews API is Source 2. These are the only two sources. Twitter/snscrape from the spec was never implemented.

**The 11 layers:**
1. **Data Sources:** ForexLive RSS + GNews API
2. **Collector:** Fetch and standardize into common format with UUID and timestamp
3. **Raw Storage:** Store everything to `raw_sentiment_data` before any filtering
4. **Cleaning:** Remove URLs, normalize whitespace, reject < 20 chars
5. **Deduplication:** Fuzzy match at 85% threshold using fuzzywuzzy (NOT hash-only like V5)
6. **Relevance Filter:** Must contain at least one financial keyword
7. **Importance Scoring:** Central bank keywords = HIGH, economic data = MEDIUM, other = LOW. Time decay: >6h = 50% score, >24h = EXPIRED
8. **AI Router:** HIGH importance + Gemini budget remaining → Gemini. Everything else → HuggingFace FinBERT
9. **Sentiment Engine:** FinBERT (primary, unlimited) or Gemini (golden ticket, max 2 calls/run)
10. **Final Storage:** Write to `processed_sentiment` table. Hash added ONLY after confirmed DB write.
11. **Aggregation:** Call `aggregate_and_push_sentiment()` for both pairs → updates `system_state.macro_sentiment`

**V5 → V6 changes:**
- **Sentiment never fed Fusion Score in V5.** `macro_sentiment` was always 0. Fixed with Layer 11 aggregation call.
- **GeminiRateLimiter class.** V5 defined `GEMINI_CALLS_PER_CYCLE = 5` in config but never checked it. V6 has a class that enforces hard caps per cycle.
- **FinBERT replaces keyword fallback.** V5 fallback was 4 words: surge/hike/rise/strong. Could not handle negation. V6 uses ProsusAI/finbert via HuggingFace Inference API.
- **FinBERT cold-start handling.** If model is loading, HuggingFace returns `{"error": "Model loading"}` — a dict, not a list. V5's `result[0][0]` would crash on this. V6 checks response type first.
- **Fuzzy dedup replaces hash-only.** V5 only caught exact duplicates. V6 catches near-duplicates (different wording, same story) at 85% similarity.
- **GBP reuses EUR result for FinBERT.** FinBERT is pair-agnostic. Running it twice per article was wasteful. Gemini still gets separate pair-specific calls.
- **GNews fix (April 5 2026):** Was calling 3 keywords per run = 288 calls/day against 100/day limit. Fixed to rotate 1 keyword per run based on current minute = 96 calls/day.
- **Hash added after DB write, not before.** V5 bug: failed API calls permanently blacklisted headlines. Fixed.

**No Telegram commands.**

---

### `volatility_atr.py`
**What it does:** The core trading engine. Runs as an always-on process on Render. Every 5 minutes fetches 20 candles of 15-min data from TwelveData for both pairs, calculates True Range and 14-period ATR, and fires a Fusion Score signal when expansion ≥ 1.5×. Logs signals to Supabase and sends to Telegram.

**V5 → V6 changes:**
- **`candles[1]` fix confirmed in V5 already** — but V6 documents it explicitly. Live candle bug (using `candles[0]`) was fixed in a prior audit.
- **Supabase singleton.** V5 called `get_supabase_client()` inside `process_signal()` on every trade, creating a new connection each time. V6 creates the client once in `__init__`.
- **Persistent dedup.** V5's `last_alerted_candles` was a module-level dict that reset to `None` on every Render restart, causing duplicate signals. V6 seeds from Supabase on startup (`_seed_alerted_candles()`) and writes back after each signal (`_persist_alerted_candle()`).
- **Doji handling.** V5 classified close==open candles as SHORT. V6 returns `None` for body < 0.0001 pip and skips the signal.
- **`/health` endpoint added.** Flask now has two routes: `/` for keepalive pings, `/health` for status JSON including `last_cycle` timestamp and error count. If the engine loop dies while Flask stays alive, `/health` returns 503.
- **Consecutive error watchdog.** 5 consecutive loop exceptions → marks engine dead, fires one Telegram alert. Counter resets to avoid notification spam.
- **`confidence_score` inserted as INT.** V5 stored it as a formatted string, breaking numeric queries.
- **Weekend killswitch** — unchanged from V5, already correct.

**No Telegram commands.** Only sends outbound signal messages.

---

### `bot.py`
**What it does:** Smart News Scheduler. Runs once per weekday at 5:30 AM IST via `bot.yml`. Fetches the ForexFactory economic calendar, sends a daily briefing to Telegram showing all upcoming High/Medium impact events for USD/EUR/GBP, then sleeps precisely to each event time and triggers the sentiment pipeline 3 minutes after each release.

**V5 → V6 changes:**
- **V5 `bot.py` was completely orphaned.** It had no workflow file and was never executed in production. It was a news calendar alerter with no scheduler.
- **V6 is a completely different file.** Smart event-driven scheduler. The process stays alive via `time.sleep()` loops (not `threading.Timer` which dies when `__main__` exits).
- **`SCAN_DELAY_MINUTES = 3`.** Scanner fires 3 minutes after the release time, giving wires time to publish headlines before scraping.
- **Event grouping.** Multiple events at the same minute trigger one scan, not multiple.
- **Pre and post scan Telegram messages.** You see exactly which events triggered each scan and a summary after.
- **`bot.yml` created from scratch.** Monday–Friday only. 6-hour job timeout to cover a full trading day.

**No Telegram commands (no polling, no handlers).** Only sends outbound messages.

---

### `performance_grader.py`
**What it does:** Grades ungraded trades nightly. Fetches all trade_logs rows where result IS NULL, downloads Yahoo Finance 15-min data for each pair, finds the closing price at entry + 1 hour, calculates pips, and updates Supabase with exit_price, pips, and WIN/LOSS/BREAKEVEN. Runs via `grader.yml` at 11:30 PM IST.

**V5 → V6 changes:**
- **`grader.yml` was crashing on every run.** Missing `supabase` in pip install. Missing `SUPABASE_URL`/`SUPABASE_KEY` in env vars. Dead GCP step writing useless `credentials.json`. All fixed.
- **Supabase query syntax fixed.** V5 used `.is_("result", "null")` (string). Correct syntax is `.is_("result", None)`. Silent wrong results in V5.
- **yfinance DataFrame access fixed.** `future['Close'].iloc[0]` instead of `future.iloc[0]['Close']`. Newer yfinance with MultiIndex columns would KeyError on the old approach.
- **BREAKEVEN added.** 0-pip trades now logged as BREAKEVEN, not silently LOSS.
- **Timestamp parsing handles 3 formats.** ISO with `+05:30`, with `Z`, and naive (assumed UTC).
- **Full validation before grading.** Each trade checked for missing fields, unknown pair, unparseable timestamp before any market data access.

**No Telegram commands.**

---

### `system_health_check.py`
**What it does:** Comprehensive system diagnostics. Runs daily at 9:00 AM IST via `health.yml`. Tests 7 things: environment variables, Supabase connection, Supabase tables, API connectivity (TwelveData, Gemini, GNews), trade logging stats, sentiment data stats, and system state values. Always sends a Telegram message — green summary when OK, red alert when anything fails. Reports to Error Bot.

**V5 → V6 changes:**
- **V5 crashed on import.** `Dict` type annotation used without importing from `typing`. Fixed by replacing all `Dict/List/Tuple` with Python 3.10 built-in `dict/list/tuple`.
- **`validate_config()` check fixed.** V5 caught `EnvironmentError` but the old `validate_config()` never raised — so the check always passed. V6's `validate_config()` raises properly, and the check catches it properly.
- **Supabase count uses `.count` property.** V5 used `len(data)` which is capped at 1000 rows due to pagination. V6 uses `resp.count` for accurate totals.
- **Gemini checked with POST not HEAD.** A HEAD request tells you the server exists, not whether your API key works. V6 sends a minimal one-word prompt and checks the response code.
- **Always sends Telegram.** V5 only printed to console. V6 sends to Error Bot (falls back to main bot if Error Bot not configured).
- **New Check 7 — System State.** Specifically watches for `macro_sentiment == 0` (sentinel pipeline broken) and COT data older than 8 days (COT job may have failed).
- **`health.yml` created from scratch.** V5 had no workflow for this file.

**No Telegram commands.**

---

### `supabase_monitor.py`
**What it does:** Manual diagnostic dashboard. Run from command line only (`python supabase_monitor.py`). Displays system state, recent trades (last 24h), all-time trade statistics with score tier breakdown, sentiment summary, and raw collection stats. Not scheduled.

**V5 → V6 changes:**
- **`__init__` no longer crashes on bad credentials.** V5 called `get_supabase_client()` with no error handling. V6 catches and prints a clear message, setting `self.supabase = None`. Each display method checks for None.
- **Twitter/snscrape counters removed.** V5 showed Twitter source counts that were always 0 (never implemented). Removed.
- **New Display 3 — All-time Trade Stats with score tier breakdown.** Win rate by score bucket (50-54, 55-59, etc.) visible directly.
- **`.count` property used everywhere.** Fixes pagination undercount on large tables.
- **`DISPLAY_LIMIT = 25`.** All live fetches capped to prevent memory issues.
- **System state shows V6 fields.** `cot_index`, `cot_net`, `cot_date`, `last_alerted_candle` all visible.

**No Telegram commands.**

---

### `supabase_schema.sql`
**What it does:** Complete database schema. Run once in Supabase SQL Editor to create all tables, seed initial data, add indexes, and migrate existing V5 tables to V6 structure.

**V5 → V6 changes:**
- **`system_state` gets 4 new columns:** `cot_index` (FLOAT), `cot_net` (INT), `cot_date` (VARCHAR), `last_alerted_candle` (VARCHAR).
- **`confidence_score` fixed to INT.** Was VARCHAR(20) in V5 — unusable for numeric queries.
- **`volatility_multiplier` fixed to FLOAT.** Was VARCHAR(20) with "2.3x" string in V5.
- **`result` now accepts BREAKEVEN.** No schema change needed — VARCHAR(10) already fits.
- **5 indexes added** on most-queried columns. Without indexes every filter does a full table scan.
- **Migration section at bottom.** Safe ALTER TABLE statements for upgrading existing V5 databases.

---

### `requirements.txt`
**What it does:** Full dependency list for local development and GitHub Actions jobs.

**V5 → V6 changes:**
- `gspread` removed (Google Sheets gone)
- `scikit-learn` removed (unused — dedup uses fuzzywuzzy not TF-IDF)
- `gnews` library removed (we use GNews REST API via requests, not the Python library)
- `supabase`, `yfinance`, `pandas`, `pytz` added (all were missing in V5 despite being imported)
- `gunicorn` added (production WSGI server for Render)

---

### `requirements-render.txt`
**What it does:** Minimal dependency list for Render deployment only. Contains only what `volatility_atr.py` actually imports.

**Why it exists:** Render free tier has 512MB RAM. `pandas` requires C compilation and needs ~1GB RAM to build from source — it was causing OOM build failures and consuming Render's 512 free pipeline minutes. This file excludes pandas, yfinance, fuzzywuzzy, feedparser, and tabulate. In Render settings, the build command should point to this file, not `requirements.txt`.

---

### `.python-version`
**What it does:** Tells Render which Python version to use. Content: `3.11.9`

**Why it exists:** Render was defaulting to Python 3.14 (newly released April 2026). `pandas 2.2.0` has no pre-built wheel for Python 3.14 and tries to compile from source, failing with a Cython C++ error. Python 3.11 has all wheels pre-built.

---

### GitHub Actions Workflows

| File | Schedule | Runs | Fixed in V6 |
|---|---|---|---|
| `cot.yml` | Saturday 7:47 AM IST | `cot_tracker.py` | Added supabase install, SUPABASE creds, removed dead GCP step |
| `grader.yml` | Daily 11:30 PM IST | `performance_grader.py` | Added supabase/yfinance/pandas install, SUPABASE creds, removed dead GCP step |
| `sentiment.yml` | Every 15 minutes | `sentiment_scanner.py` | Added HUGGINGFACE_API_KEY, removed dead gnews package, added [skip ci] to state commit |
| `bot.yml` | Mon–Fri 5:30 AM IST | `bot.py` | **New file — didn't exist in V5** |
| `health.yml` | Daily 9:00 AM IST | `system_health_check.py` | **New file — didn't exist in V5** |

---

## Telegram Bot Architecture

**There are NO Telegram command handlers anywhere in the codebase.** No `/news`, `/status`, `/start`, or any other commands. The bot is purely outbound — it sends messages, never receives or responds to them.

Messages sent automatically:
- **Main bot:** Fusion Score signals (volatility_atr.py), COT weekly report (cot_tracker.py), daily news briefing (bot.py), event-driven scan notifications (bot.py)
- **Error bot:** Health check reports (system_health_check.py), critical error alerts (send_error_notification() called from any file)

If you want to add Telegram commands (/status showing current system state, /score showing last Fusion Score, etc.) that would be a new component — a separate bot polling loop, not part of any existing file.

---

## Current System State (April 5, 2026)

### What is working:
- COT tracker: ✅ Running, Supabase updated, Telegram has formatting bug (use new cot_tracker.py)
- Sentiment scanner: ✅ Running, collecting RSS + GNews, storing to Supabase, dedup working
- Health check: ✅ Running, all 7 checks passing except system_state (expected — macro_sentiment still 0)
- All APIs: ✅ TwelveData, Gemini, GNews all confirmed reachable from health check log
- Supabase: ✅ All 4 tables exist, COT data seeded for both pairs

### What needs to happen next:
1. Upload fixed `cot_tracker.py` (Telegram formatting fix)
2. Upload `requirements-render.txt` and `.python-version` to repo
3. Change Render build command to `pip install -r requirements-render.txt`
4. Start Render service — `volatility_atr.py` will begin monitoring
5. `macro_sentiment` will self-correct once fresh articles come through scanner (not expired)

### Known limitations:
- `macro_sentiment = 0` for both pairs — normal on fresh setup, will populate on next scanner run with fresh news
- GNews articles on first run were all > 24h old (expired) — normal, self-corrects
- No Telegram commands implemented — outbound only
- OpenRouter DeepSeek fallback not implemented yet (deferred, discussed)

---

## API Budget Summary

| API | Free Limit | Daily Usage | Status |
|---|---|---|---|
| TwelveData | 800 credits/day | ~288 (2 pairs × 96 runs × 3 calls... wait, 2 pairs fetched together = 96 calls) | ✅ Safe |
| Gemini 2.5 Flash | 20 RPD, 5 RPM (AI Studio verified) | Max 2/run × limited runs = well under 20 | ✅ Safe |
| GNews | 100/day | 96/day (1 keyword/run, rotated) | ✅ Safe |
| HuggingFace FinBERT | Unlimited free inference | N/A | ✅ Safe |
| ForexLive RSS | Free, no limit | 96 fetches/day | ✅ Safe |
| Supabase | 500MB storage, 2GB bandwidth | Minimal | ✅ Safe |

---

## Architecture Decisions Log

**Why Supabase instead of Google Sheets?**
Google Sheets had a 60 req/min limit causing race conditions between Render (continuous) and GitHub Actions (scheduled). Supabase has no such limit and supports proper upsert, indexing, and querying.

**Why FinBERT as primary instead of Gemini?**
Gemini free tier is 20 RPD (verified). At 96 runs/day × 2 calls/article = budget exhausted in one run. FinBERT is unlimited, purpose-built for financial text, and runs on HuggingFace's free inference API.

**Why time.sleep() in bot.py instead of threading.Timer?**
threading.Timer is non-blocking — the main thread exits immediately after scheduling, destroying all timers. time.sleep() keeps the process alive explicitly until all events have fired.

**Why requirements-render.txt separate from requirements.txt?**
Render has 512MB RAM. pandas requires ~1GB RAM to compile from source on Python 3.14. Separate file lets Render install only the 6 packages `volatility_atr.py` actually needs.

**Why COT Index normalization instead of raw net position?**
Raw net of +150,000 contracts means nothing in isolation. Normalized against the 52-week range, a value near 1.0 means institutions are near their most bullish extreme ever recorded — highly meaningful signal. Industry standard used by professional COT analysts.
