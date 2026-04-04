"""
bot.py - Smart News Scheduler & Event-Driven Sentiment Trigger
Fusion Score Bot V6.0

Architecture: Single long-running GitHub Actions job.
    1. Fetches today's ForexFactory economic calendar at startup
    2. Sends a daily briefing to Telegram (all High + Medium events)
    3. Sleeps precisely until each news event fires
    4. At each event: triggers a targeted sentiment pipeline run
    5. Process stays alive via time.sleep() until all events are done

GitHub Actions job timeout: 6 hours max.
This is fine — most trading days have all events within a 6-hour window.
For days with events spread > 6 hours, the scheduler covers the first window.

Design decision: time.sleep() loop (not threading.Timer).
threading.Timer arms callbacks and returns — the process would exit
immediately after __main__ completes, destroying all timers before
they fire. The sleep loop keeps the process alive explicitly.
"""

import time
import logging
import requests
from datetime import datetime, timezone, timedelta

import telebot

from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    validate_config
)
from shared_functions import send_error_notification
from sentiment_scanner import SentimentScannerPipeline

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# IST timezone offset
IST = timezone(timedelta(hours=5, minutes=30))

# ForexFactory public calendar endpoint
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
    )
}

# Currencies and impact levels we care about
TARGET_CURRENCIES = {'USD', 'EUR', 'GBP'}
TARGET_IMPACTS    = {'High', 'Medium'}

# How many minutes after the news release to trigger the scanner
# Gives headlines time to hit the wires before we scrape them
SCAN_DELAY_MINUTES = 3


# =============================================================================
# SECTION 1: CALENDAR FETCHING
# =============================================================================

def fetch_todays_schedule() -> list:
    """
    Fetches the ForexFactory calendar and returns today's relevant events,
    sorted by time, filtered to upcoming events only.

    Returns:
        List of event dicts:
            {
                "time":     datetime (IST, timezone-aware),
                "currency": str,
                "impact":   str,
                "title":    str
            }
        Sorted ascending by time.
        Only includes events that haven't passed yet.
    """
    try:
        response = requests.get(
            CALENDAR_URL,
            headers=CALENDAR_HEADERS,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, list):
            logger.error("[Calendar] Unexpected response format.")
            return []

    except Exception as e:
        logger.error(f"[Calendar] Fetch failed: {e}")
        send_error_notification(f"Bot: Calendar fetch failed: {e}")
        return []

    now        = datetime.now(IST)
    today_date = now.date()
    events     = []

    for raw in data:
        currency = raw.get('country', '')
        impact   = raw.get('impact', '')

        if currency not in TARGET_CURRENCIES or impact not in TARGET_IMPACTS:
            continue

        raw_date = raw.get('date', '')
        if not raw_date:
            continue

        try:
            # ForexFactory dates are UTC — convert to IST
            event_time_utc = datetime.fromisoformat(
                raw_date.replace('Z', '+00:00')
            )
            event_time_ist = event_time_utc.astimezone(IST)
        except (ValueError, AttributeError):
            logger.warning(f"[Calendar] Could not parse date: {raw_date}")
            continue

        # Only today's events that haven't passed yet
        if event_time_ist.date() != today_date:
            continue
        if event_time_ist <= now:
            continue

        events.append({
            "time":     event_time_ist,
            "currency": currency,
            "impact":   impact,
            "title":    raw.get('title', 'Unknown Event')
        })

    # Sort ascending — earliest event first
    events.sort(key=lambda e: e['time'])

    logger.info(f"[Calendar] Found {len(events)} upcoming events today.")
    return events


# =============================================================================
# SECTION 2: TELEGRAM DAILY BRIEFING
# =============================================================================

def send_daily_briefing(bot: telebot.TeleBot, events: list):
    """
    Sends a formatted daily briefing showing all today's events
    and when the scanner will fire for each.
    """
    if not events:
        try:
            bot.send_message(
                TELEGRAM_CHAT_ID,
                "📊 *QUANT RADAR — Daily Briefing*\n\n"
                "✅ No upcoming High or Medium impact events "
                "for USD, EUR, or GBP today.\n"
                "_Sentiment scanner running on standard 15-min schedule._",
                parse_mode="Markdown",
                timeout=10
            )
        except Exception as e:
            logger.error(f"[Briefing] Telegram send failed: {e}")
        return

    impact_emoji = {"High": "🔴", "Medium": "🟠"}

    msg  = "📊 *QUANT RADAR — Event-Driven Schedule* 📊\n"
    msg += f"_Armed for {len(events)} events today_\n\n"

    current_time_block = None

    for event in events:
        time_str = event['time'].strftime('%I:%M %p')
        scan_str = (event['time'] + timedelta(minutes=SCAN_DELAY_MINUTES)).strftime('%I:%M %p')

        # Group events at the same minute into one time block
        if time_str != current_time_block:
            msg += f"⏰ *{time_str} IST*\n"
            current_time_block = time_str

        emoji = impact_emoji.get(event['impact'], '⚪')
        msg  += f" ├─ {emoji} {event['currency']} — {event['title']}\n"
        msg  += f" └─ _Scanner fires at {scan_str} IST_\n\n"

    msg += "─────────────────────\n"
    msg += "_Scanner will run immediately after each event._"

    try:
        bot.send_message(
            TELEGRAM_CHAT_ID, msg,
            parse_mode="Markdown",
            timeout=10
        )
        logger.info("[Briefing] Daily briefing sent to Telegram.")
    except Exception as e:
        logger.error(f"[Briefing] Telegram send failed: {e}")


# =============================================================================
# SECTION 3: EVENT-DRIVEN SCANNER TRIGGER
# =============================================================================

def run_targeted_scan(bot: telebot.TeleBot, events_fired: list):
    """
    Triggers the sentiment pipeline.
    Sends a pre-scan notification and a post-scan summary.

    Args:
        events_fired: List of event dicts that just triggered this scan.
    """
    # Pre-scan notification
    event_lines = "\n".join(
        f"  • {e['currency']} {e['impact']}: {e['title']}"
        for e in events_fired
    )

    try:
        bot.send_message(
            TELEGRAM_CHAT_ID,
            f"⚡ *EVENT-DRIVEN SCAN TRIGGERED*\n\n"
            f"News just released:\n{event_lines}\n\n"
            f"_Running sentiment pipeline now..._",
            parse_mode="Markdown",
            timeout=10
        )
    except Exception as e:
        logger.error(f"[Scanner] Pre-scan notification failed: {e}")

    # Run pipeline
    logger.info(f"[Scanner] Triggering pipeline for {len(events_fired)} event(s).")

    try:
        pipeline = SentimentScannerPipeline()
        result   = pipeline.run_pipeline()

        processed = result.get('processed', 0)
        gemini    = result.get('gemini_calls', 0)

        # Post-scan summary
        try:
            bot.send_message(
                TELEGRAM_CHAT_ID,
                f"✅ *Scan Complete*\n\n"
                f"📰 Articles processed: `{processed}`\n"
                f"🤖 Gemini calls used: `{gemini}/{2}`\n"
                f"_Fusion Score updated with latest sentiment._",
                parse_mode="Markdown",
                timeout=10
            )
        except Exception as e:
            logger.error(f"[Scanner] Post-scan notification failed: {e}")

    except Exception as e:
        logger.error(f"[Scanner] Pipeline failed: {e}")
        send_error_notification(f"Event-Driven Scan Failed: {e}")


# =============================================================================
# SECTION 4: MAIN SCHEDULER LOOP
# =============================================================================

def run_scheduler():
    """
    Main event loop. Keeps the process alive until all events have fired.

    Logic:
        - Each iteration checks which events are due within the next 60 seconds
        - Groups simultaneous events (same minute) into a single scan trigger
        - Sleeps to the next event time, waking up 5 seconds early to be precise
        - Exits cleanly when all events are processed

    Why time.sleep() and not threading.Timer():
        threading.Timer() is non-blocking — the main thread finishes and
        the process exits, destroying all pending timers before they fire.
        time.sleep() keeps this process alive until we're done.
    """
    try:
        validate_config('bot')
        bot = telebot.TeleBot(TELEGRAM_TOKEN)
        logger.info("[Scheduler] Bot initialized.")
    except Exception as e:
        logger.error(f"[Scheduler] Init failed: {e}")
        return

    # Fetch today's schedule
    events = fetch_todays_schedule()

    # Send daily briefing regardless of whether events exist
    send_daily_briefing(bot, events)

    if not events:
        logger.info("[Scheduler] No upcoming events. Job complete.")
        return

    # Track which events have fired
    pending = list(events)   # copy so we can pop safely

    logger.info(f"[Scheduler] Entering event loop. {len(pending)} events pending.")

    while pending:
        now = datetime.now(IST)

        # Collect all events whose scan time (event + delay) has passed or
        # is within the next 60 seconds
        scan_time_threshold = now + timedelta(seconds=60)
        due_events = []
        still_pending = []

        for event in pending:
            scan_time = event['time'] + timedelta(minutes=SCAN_DELAY_MINUTES)

            if scan_time <= scan_time_threshold:
                due_events.append(event)
            else:
                still_pending.append(event)

        if due_events:
            logger.info(
                f"[Scheduler] Firing scan for {len(due_events)} event(s)."
            )
            run_targeted_scan(bot, due_events)
            pending = still_pending

            if not pending:
                logger.info("[Scheduler] All events processed. Job complete.")
                break

        # Sleep until next event's scan time (wake 5 seconds early)
        if pending:
            next_scan_time = (
                pending[0]['time'] + timedelta(minutes=SCAN_DELAY_MINUTES)
            )
            sleep_seconds = (next_scan_time - datetime.now(IST)).total_seconds() - 5

            if sleep_seconds > 0:
                logger.info(
                    f"[Scheduler] Sleeping {sleep_seconds:.0f}s until next "
                    f"event: {pending[0]['title']} "
                    f"@ {pending[0]['time'].strftime('%I:%M %p IST')}"
                )
                time.sleep(sleep_seconds)
            else:
                # Next event is imminent — tight loop
                time.sleep(5)

    logger.info("[Scheduler] Smart News Scheduler complete.")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    run_scheduler()
