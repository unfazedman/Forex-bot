"""
performance_grader.py - Automated Trade Grading Engine
Fusion Score Bot V6.0

Runs nightly via GitHub Actions (grader.yml).
Grades all ungraded trades in Supabase by fetching exit price
from TwelveData at the 1-hour mark after entry.

Why TwelveData instead of yfinance:
    - TwelveData is already the production data source for signals
    - yfinance uses Yahoo Finance undocumented API (breaks without warning)
    - Same source for entry AND exit = consistent pricing
    - No pandas or C-compilation required — lightweight install

TwelveData usage per grader run:
    - 2 API calls (one per pair), each fetching 500 x 15min candles
    - 500 candles = ~5 days of data, enough for any exit candle
    - 2 calls/day well within 800 credit free tier limit
"""

import requests
import logging
import pytz
from datetime import datetime, timedelta

from shared_functions import get_supabase_client, send_error_notification
from config import TWELVE_DATA_KEY, validate_config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TD_SYMBOLS    = {'EUR/USD': 'EUR/USD', 'GBP/USD': 'GBP/USD'}
HOLD_HOURS    = 1
PIP_MULTIPLIER = 10_000
IST           = pytz.timezone('Asia/Kolkata')


def fetch_candles_twelvedata(pair: str) -> list:
    """
    Fetches last 500 x 15-min candles from TwelveData for a pair.
    Returns list sorted oldest to newest, or empty list on failure.
    Each candle dict has 'datetime' (IST string) and 'close' (string).
    """
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={pair}&interval=15min&outputsize=500"
        f"&timezone=Asia/Kolkata"
        f"&apikey={TWELVE_DATA_KEY}"
    )
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()

        if data.get('status') == 'error':
            logger.error(f"[MarketData] TwelveData error for {pair}: {data.get('message')}")
            return []

        candles = data.get('values', [])
        if not candles:
            logger.warning(f"[MarketData] No candles returned for {pair}")
            return []

        candles.reverse()  # newest-first → oldest-first
        logger.info(f"[MarketData] {pair}: {len(candles)} candles fetched.")
        return candles

    except Exception as e:
        logger.error(f"[MarketData] Failed to fetch {pair}: {e}")
        return []


def find_exit_price(candles: list, exit_time_ist: datetime) -> float:
    """
    Finds close price of first candle at or after exit_time_ist.
    TwelveData datetime format: '2026-04-07 10:15:00' (IST when timezone param used).

    Raises ValueError if no candle found.
    """
    exit_naive = exit_time_ist.replace(tzinfo=None)

    for candle in candles:
        try:
            candle_dt = datetime.strptime(candle['datetime'], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
        if candle_dt >= exit_naive:
            price = float(candle['close'])
            logger.info(f"[Grader] Exit candle: {candle['datetime']} close={price:.5f}")
            return price

    raise ValueError(
        f"No candle at or after {exit_time_ist.strftime('%Y-%m-%d %H:%M IST')}"
    )


def parse_entry_time(timestamp_str: str) -> datetime:
    """Parses Supabase timestamp to IST datetime. Handles Z, +05:30, naive."""
    ts = timestamp_str.replace('Z', '+00:00')
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(IST)


def calculate_result(direction: str, entry: float, exit_price: float) -> tuple:
    """Returns (pips, 'WIN'|'LOSS'|'BREAKEVEN')."""
    pips = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
    pips = round(pips * PIP_MULTIPLIER, 1)
    result = "WIN" if pips > 0 else ("LOSS" if pips < 0 else "BREAKEVEN")
    return pips, result


def grade_trades():
    """Main grading loop."""
    logger.info("[Grader] ===== Performance Grader Starting =====")

    try:
        validate_config('performance_grader')
        supabase = get_supabase_client()
    except Exception as e:
        send_error_notification(f"Performance Grader Init Failed: {e}")
        logger.error(e)
        return

    # Fetch ungraded trades
    try:
        response = (
            supabase.table("trade_logs")
            .select("*")
            .is_("result", None)
            .not_.is_("entry_price", None)
            .execute()
        )
        ungraded = response.data or []
    except Exception as e:
        send_error_notification(f"Grader: Failed to fetch trades: {e}")
        logger.error(e)
        return

    if not ungraded:
        logger.info("[Grader] No ungraded trades. Standing by.")
        return

    logger.info(f"[Grader] {len(ungraded)} ungraded trade(s) found.")

    # Fetch TwelveData candles once per pair — reuse across all trades
    candle_cache = {pair: fetch_candles_twelvedata(pair) for pair in TD_SYMBOLS}

    now_ist = datetime.now(IST)
    graded = skipped = 0

    for trade in ungraded:
        trade_id    = trade.get('id')
        pair        = trade.get('pair')
        direction   = trade.get('direction')
        entry_price = trade.get('entry_price')
        timestamp   = trade.get('timestamp_ist')

        if not all([trade_id, pair, direction, entry_price, timestamp]):
            logger.warning(f"[Grader] Trade {trade_id}: missing fields. Skipping.")
            skipped += 1
            continue

        if pair not in TD_SYMBOLS:
            logger.warning(f"[Grader] Unknown pair {pair}. Skipping {trade_id}.")
            skipped += 1
            continue

        try:
            entry_time = parse_entry_time(timestamp)
        except Exception as e:
            logger.warning(f"[Grader] Trade {trade_id}: time error: {e}. Skipping.")
            skipped += 1
            continue

        exit_time = entry_time + timedelta(hours=HOLD_HOURS)

        if now_ist < exit_time:
            logger.info(f"[Grader] Trade {trade_id} still open until {exit_time.strftime('%H:%M IST')}.")
            skipped += 1
            continue

        candles = candle_cache.get(pair, [])
        if not candles:
            logger.warning(f"[Grader] No candles for {pair}. Skipping {trade_id}.")
            skipped += 1
            continue

        try:
            exit_price = find_exit_price(candles, exit_time)
        except ValueError as e:
            logger.info(f"[Grader] Trade {trade_id}: {e}. Skipping.")
            skipped += 1
            continue

        try:
            entry_float = float(entry_price)
        except (TypeError, ValueError):
            logger.warning(f"[Grader] Trade {trade_id}: bad entry_price. Skipping.")
            skipped += 1
            continue

        pips, result = calculate_result(direction, entry_float, exit_price)

        logger.info(
            f"[Grader] {trade_id}: {pair} {direction} | "
            f"Entry: {entry_float:.5f} | Exit: {exit_price:.5f} | "
            f"Pips: {pips:+.1f} | {result}"
        )

        try:
            supabase.table("trade_logs").update({
                "exit_price": round(exit_price, 5),
                "pips":       pips,
                "result":     result
            }).eq("id", trade_id).execute()
            graded += 1
        except Exception as e:
            logger.error(f"[Grader] DB update failed for {trade_id}: {e}")
            send_error_notification(f"Grader DB Update Failed (trade {trade_id}): {e}")

    logger.info(f"[Grader] Complete. Graded: {graded} | Skipped: {skipped}")
    logger.info("[Grader] ===== Performance Grader Complete =====")


if __name__ == "__main__":
    grade_trades()
