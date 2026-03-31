# Forex-bot Line-by-Line Audit Report

## 1. `config.py`
- **Imports**: `import os` (Used ✅)
- **API Keys**: Loaded from `os.environ`. Standard practice.
- **Parameters**: `ATR_THRESHOLD = 1.5`. (Formulaic constant, used in `volatility_atr.py`).

## 2. `bot.py`
- **Imports**: `import telebot`, `requests`, `datetime`, `os` (All used ✅).
- **API Call**: `https://nfs.faireconomy.media/ff_calendar_thisweek.json` (Forex Factory).
  - **Free Tier Limit**: This is a public JSON feed. No documented API key or hard limit, but aggressive polling can lead to IP bans (as noted in `volatility_atr.py` firewall warning).
- **Logic/Formula**:
  - `ist_offset = timezone(timedelta(hours=5, minutes=30))` (Correct for IST ✅).
  - `if ist_time.date() == today_date:` (Correct filtering ✅).

## 3. `shared_functions.py`
- **Imports**: `import os`, `json`, `gspread`, `telebot` (All used ✅).
- **Logic/Formula**:
  - `calculate_fusion_score`:
    - **FLAG**: Lines 35-39:
      ```python
      if pair_direction == "LONG":
          if sentiment <= -5: score += WEIGHT_SENTIMENT 
          elif sentiment >= 5: score -= WEIGHT_SENTIMENT 
      else: # SHORT
          if sentiment >= 5: score += WEIGHT_SENTIMENT   
          elif sentiment <= -5: score -= WEIGHT_SENTIMENT
      ```
      - **Audit**: If `sentiment` is +10 (Bullish USD), and we are `LONG` EUR/USD (Short USD), the score *decreases*. This is mathematically consistent if sentiment is USD-based. However, `sentiment_scanner.py` scores impact on "EUR/USD price". If +10 means Bullish EUR, then a LONG should *increase* the score. **Confirm sentiment polarity.**

## 4. `volatility_atr.py`
- **Imports**: `import requests`, `time`, `os`, `threading`, `pytz`, `datetime`, `flask`, `telebot` (All used ✅).
- **API Call**: `https://api.twelvedata.com/time_series`
  - **Free Tier Limit**: **CRITICAL FLAG**. Twelve Data Free Tier allows **800 API credits per day**.
  - **Usage**: `while True: analyze_volatility(); time.sleep(240)`. 240s = 4 minutes.
  - **Math**: (1440 min / 4 min) = 360 calls/day. With 2 pairs (`EUR/USD`, `GBP/USD`), that's **720 calls/day**. You are very close to the 800 limit. If you add a third pair or reduce sleep, you will hit 429 errors.
- **Formula**:
  - `calculate_tr`: `max(high - low, abs(high - prev_close), abs(low - prev_close))` (Correct True Range formula ✅).
  - `atr_14`: `sum(trs) / len(trs)` (This is a Simple Moving Average of TR. Standard ATR uses Wilder's Smoothing. Results will differ slightly from TradingView/MT4 ✅/⚠️).

## 5. `sentiment_scanner.py`
- **Imports**: `import requests`, `feedparser`, `time`, `os`, `re`, `json`, `logging`, `hashlib`, `telebot`, `datetime` (All used ✅).
- **API Call**: `gemini-2.5-flash:generateContent`
  - **Free Tier Limit**: 15 RPM (Requests Per Minute), 1 million TPM, 1,500 RPD (Requests Per Day).
  - **Usage**: Triggered by GitHub Action every 15 mins. Each run scans RSS. 15s sleep between headlines. Well within limits ✅.
- **Logic**: `re.search(r'-?\d+', raw_text)` (Robust enough for single integer extraction ✅).

## 6. `cot_tracker.py`
- **Imports**: `import requests`, `telebot`, `os`, `gspread` (All used ✅).
- **API Call**: `https://publicreporting.cftc.gov/resource/6dca-aqww.json` (Socrata API).
  - **Free Tier Limit**: No hard limit for public datasets, but throttled. Run once per week (COT updates Fridays). Safe ✅.
- **Formula**: `longs - shorts` (Standard Net Position ✅).

## 7. `performance_grader.py`
- **Imports**: `import yfinance as yf`, `import pandas as pd`, `datetime`, `pytz`, `logging` (All used ✅).
- **FLAG**: `yfinance` and `pandas` are **NOT** in `requirements.txt`. The GitHub Action will fail if it tries to run this.
- **Formula**:
  - `pips = (exit_price - entry_price) * 10000`
  - **Audit**: This is correct for 4/5 decimal pairs (EURUSD, GBPUSD). If you ever add JPY pairs (USDJPY), this must be `* 100`.
- **Logic**: `exit_time = entry_time + timedelta(hours=1)` (Matches strategy ✅).

## 8. `requirements.txt`
- **FLAG**: Missing `yfinance`, `pandas`, `pytz`.
- **Audit**: `flask` is included but only used in `volatility_atr.py` (which isn't the main GH Action script).

---
**Summary of Critical Issues:**
1. **Twelve Data Quota**: You are at ~90% capacity on the free tier.
2. **Missing Dependencies**: `performance_grader.py` will crash in production due to missing `yfinance/pandas`.
3. **Sentiment Polarity**: Verify if Gemini's +10 means "EUR/USD goes up" or "USD is strong". Currently, `shared_functions.py` assumes +10 is "USD is strong".
