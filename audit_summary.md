# Comprehensive Audit Summary: Forex-bot v4.0

This document consolidates findings from my line-by-line audit, the Anthropic audit, and the System Architecture KT document.

## 1. Critical Bugs & Logic Errors
- **Signal Integrity (Live Candle)**: `volatility_atr.py` triggers on `candles[0]`, which is an incomplete, live 15m candle. **Fix**: Use `candles[1]` (last closed) and `candles[2]` (previous close).
- **Sentiment Mismatch**: `sentiment_scanner.py` scores headlines only for EUR/USD impact but applies the same score to GBP/USD. **Fix**: Run separate Gemini prompts for each pair.
- **Sentiment Polarity**: In `shared_functions.py`, sentiment scoring for SHORT positions might be inverted depending on Gemini's output definition (Bullish EUR vs Bullish USD).
- **Sleep Timer**: `volatility_atr.py` uses 240s (4 min) while the KT doc requires 300s (5 min) to protect the 800-credit Twelve Data limit.
- **Race Conditions**: `state.json` in GitHub Actions and in-memory `last_alerted_candles` on Render are prone to data loss and duplicate signals.

## 2. Mathematical & Formulaic Issues
- **ATR Calculation**: Current code uses a Simple Moving Average (SMA) of True Range. Standard ATR uses Wilder's Smoothed Moving Average.
- **COT Neutral Band**: `cot_tracker.py` lacks a neutral threshold; any net position > 0 is "BULLISH", which is noisy. **Fix**: Add a threshold (e.g., 10,000 contracts).
- **Pip Calculation**: `performance_grader.py` assumes 10,000 scaling for all pairs. This will fail for JPY pairs (requires 100).

## 3. Infrastructure & Database (Phase 5 Migration)
- **Google Sheets Bottleneck**: Rigid 60 req/min limit and async race conditions between GitHub Actions and Render.
- **Missing Dependencies**: `requirements.txt` is missing `yfinance`, `pandas`, `pytz`, and will soon need `supabase`.
- **Supabase Integration**:
  - Integrate `SUPABASE_URL` and `SUPABASE_KEY` into `config.py`.
  - Rewrite `shared_functions.py` to use `supabase-py` instead of `gspread`.
  - Update all CRUD operations in execution, sentiment, and grader scripts.

## 4. Immediate Action Plan
1. **Initialize Supabase**: Update `config.py` and `shared_functions.py`.
2. **Refactor Core Logic**: Fix live candle triggering and separate sentiment scoring.
3. **Stabilize Infrastructure**: Standardize sleep timers, fix dependencies, and move state to Supabase.
4. **Audit Formulas**: Update ATR to Wilder's smoothing and add COT neutral bands.
