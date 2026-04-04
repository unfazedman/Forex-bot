"""
performance_grader.py - Automated Trade Grading Engine
Fusion Score Bot V6.0

Runs nightly via GitHub Actions (grader.yml).
Grades all ungraded trades in Supabase by downloading exit price
from Yahoo Finance at the 1-hour mark after entry.

Strategy assumption: trades are held for exactly 1 hour.

Key fixes from V5 audit:
    - Supabase query uses None (not string "null") for IS NULL checks
    - yfinance DataFrame access uses .iloc[0]['Close'] not ['Close'].iloc[0]
      (avoids MultiIndex KeyError in newer yfinance versions)
    - Weekend/gap handling: skips grading if exit candle is not available yet
    - 0-pip trades logged as BREAKEVEN, not silently LOSS
    - Supabase credentials properly passed via grader.yml env vars
    - supabase package installed in grader.yml (was missing in V5)
    - No gspread dependency (removed Google Sheets leftover)
"""

import logging
import pytz
from datetime import datetime, timedelta

import yfinance as yf
import pandas as pd

from shared_functions import get_supabase_client, send_error_notification
from config import validate_config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Yahoo Finance ticker mapping
YF_TICKERS = {
    'EUR/USD': 'EURUSD=X',
    'GBP/USD': 'GBPUSD=X'
}

# Strategy hold time
HOLD_HOURS = 1

# Pip scaling factor (EUR/USD and GBP/USD are 4/5 decimal pairs)
# If JPY pairs are ever added, this must be 100 for those pairs
PIP_MULTIPLIER = 10_000

# IST timezone
IST = pytz.timezone('Asia/Kolkata')


def download_market_data() -> dict:
    """
    Downloads last 5 days of 15-min OHLCV data for all pairs.
    Converts index to IST to match trade log timestamps.

    Returns:
        Dict mapping pair string → DataFrame (IST-indexed)
        Empty dict entry if download fails for a pair.
    """
    market_data = {}

    for pair, ticker in YF_TICKERS.items():
        try:
            df = yf.download(
                ticker,
                period="5d",
                interval="15m",
                progress=False,
                auto_adjust=True
            )

            if df.empty:
                logger.warning(f"[MarketData] No data returned for {ticker}")
                market_data[pair] = pd.DataFrame()
                continue

            # Localize to UTC if naive, then convert to IST
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC')
            df.index = df.index.tz_convert(IST)

            # yfinance with auto_adjust=True may return MultiIndex columns
            # Flatten to simple column names if needed
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            market_data[pair] = df
            logger.info(f"[MarketData] {pair}: {len(df)} candles downloaded.")

        except Exception as e:
            logger.error(f"[MarketData] Failed to download {ticker}: {e}")
            market_data[pair] = pd.DataFrame()

    return market_data


def parse_entry_time(timestamp_str: str) -> datetime:
    """
    Parses the entry timestamp from Supabase to an IST-aware datetime.

    Handles:
        - ISO format with timezone info ("2026-04-01T14:30:00+05:30")
        - ISO format with Z suffix ("2026-04-01T09:00:00Z")
        - Naive ISO format (assumed UTC)

    Returns:
        datetime in IST timezone

    Raises:
        ValueError if the string cannot be parsed
    """
    ts = timestamp_str.replace('Z', '+00:00')
    dt = datetime.fromisoformat(ts)

    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)

    return dt.astimezone(IST)


def get_exit_price(df: pd.DataFrame, exit_time: datetime) -> float:
    """
    Finds the closing price of the first 15-min candle at or after exit_time.

    Args:
        df:        IST-indexed DataFrame with a 'Close' column
        exit_time: Target exit datetime (IST)

    Returns:
        float exit price

    Raises:
        ValueError if no candle is available at or after exit_time
    """
    future = df[df.index >= exit_time]

    if future.empty:
        raise ValueError(f"No market data available at or after {exit_time}")

    # Safe access regardless of MultiIndex or single-level columns
    close_value = future['Close'].iloc[0]

    # Handle scalar vs Series return (yfinance version differences)
    if hasattr(close_value, 'item'):
        return float(close_value.item())
    return float(close_value)


def calculate_result(direction: str, entry: float, exit_price: float) -> tuple:
    """
    Calculates pips and result for a trade.

    Returns:
        (pips: float, result: str)
        result is one of: "WIN", "LOSS", "BREAKEVEN"
    """
    if direction == "LONG":
        pips = (exit_price - entry) * PIP_MULTIPLIER
    else:
        pips = (entry - exit_price) * PIP_MULTIPLIER

    pips = round(pips, 1)

    if pips > 0:
        result = "WIN"
    elif pips < 0:
        result = "LOSS"
    else:
        result = "BREAKEVEN"

    return pips, result


def grade_trades():
    """
    Main grading function.

    Flow:
        1. Connect to Supabase
        2. Fetch all ungraded trades (result IS NULL, entry_price IS NOT NULL)
        3. Download market data for all pairs
        4. For each trade:
            a. Parse entry time
            b. Calculate exit time (entry + 1 hour)
            c. Skip if trade is still open
            d. Find exit candle in market data
            e. Calculate pips and result
            f. Update Supabase
    """
    logger.info("[Grader] ===== Performance Grader Starting =====")

    try:
        validate_config('performance_grader')
        supabase = get_supabase_client()
    except Exception as e:
        error_msg = f"Performance Grader: Init failed: {e}"
        logger.error(error_msg)
        send_error_notification(error_msg)
        return

    # ---- Fetch ungraded trades ----
    # V5 bug: used string "null" in .is_() calls.
    # Correct Supabase Python client syntax uses None.
    try:
        response = (
            supabase
            .table("trade_logs")
            .select("*")
            .is_("result", None)
            .not_.is_("entry_price", None)
            .execute()
        )
        ungraded = response.data if response.data else []
    except Exception as e:
        error_msg = f"Performance Grader: Failed to fetch ungraded trades: {e}"
        logger.error(error_msg)
        send_error_notification(error_msg)
        return

    if not ungraded:
        logger.info("[Grader] No ungraded trades found. Standing by.")
        return

    logger.info(f"[Grader] Found {len(ungraded)} ungraded trade(s).")

    # ---- Download market data ----
    market_data = download_market_data()

    now_ist = datetime.now(IST)
    graded_count = 0
    skipped_count = 0

    # ---- Grade each trade ----
    for trade in ungraded:
        trade_id    = trade.get('id')
        pair        = trade.get('pair')
        direction   = trade.get('direction')
        entry_price = trade.get('entry_price')
        timestamp   = trade.get('timestamp_ist')

        # Basic validation
        if not all([trade_id, pair, direction, entry_price, timestamp]):
            logger.warning(f"[Grader] Trade {trade_id}: missing fields. Skipping.")
            skipped_count += 1
            continue

        if pair not in YF_TICKERS:
            logger.warning(f"[Grader] Unknown pair '{pair}'. Skipping trade {trade_id}.")
            skipped_count += 1
            continue

        # Parse entry time
        try:
            entry_time = parse_entry_time(timestamp)
        except Exception as e:
            logger.warning(f"[Grader] Trade {trade_id}: time parse error: {e}. Skipping.")
            skipped_count += 1
            continue

        # Calculate exit time
        exit_time = entry_time + timedelta(hours=HOLD_HOURS)

        # Skip trades still open
        if now_ist < exit_time:
            logger.info(
                f"[Grader] Trade {trade_id} ({pair}) still open until "
                f"{exit_time.strftime('%H:%M IST')}. Skipping."
            )
            skipped_count += 1
            continue

        # Get market data for this pair
        df = market_data.get(pair, pd.DataFrame())
        if df.empty:
            logger.warning(f"[Grader] No market data for {pair}. Skipping trade {trade_id}.")
            skipped_count += 1
            continue

        # Find exit price
        try:
            exit_price = get_exit_price(df, exit_time)
        except ValueError as e:
            logger.info(f"[Grader] Trade {trade_id}: {e}. Skipping.")
            skipped_count += 1
            continue

        # Calculate result
        try:
            entry_float = float(entry_price)
        except (TypeError, ValueError):
            logger.warning(f"[Grader] Trade {trade_id}: invalid entry_price. Skipping.")
            skipped_count += 1
            continue

        pips, result = calculate_result(direction, entry_float, exit_price)

        logger.info(
            f"[Grader] Trade {trade_id}: {pair} {direction} | "
            f"Entry: {entry_float:.5f} | Exit: {exit_price:.5f} | "
            f"Pips: {pips:+.1f} | {result}"
        )

        # Update Supabase
        try:
            supabase.table("trade_logs").update({
                "exit_price": round(exit_price, 5),
                "pips":       pips,
                "result":     result
            }).eq("id", trade_id).execute()

            graded_count += 1

        except Exception as e:
            logger.error(f"[Grader] Failed to update trade {trade_id}: {e}")
            send_error_notification(f"Grader DB Update Failed (trade {trade_id}): {e}")

    logger.info(
        f"[Grader] Complete. Graded: {graded_count} | Skipped: {skipped_count}"
    )
    logger.info("[Grader] ===== Performance Grader Complete =====")


if __name__ == "__main__":
    grade_trades()
