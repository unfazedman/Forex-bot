"""
cot_tracker.py - Smart Money Positioning Tracker
Fusion Score Bot V6.0

Industry Standard: CFTC Commitments of Traders (COT) Analysis
Architecture: Full 52-week index normalization + 5-state momentum classification

COT Index Formula (industry standard):
    index = (current_net - min_52w) / (max_52w - min_52w)
    Result: 0.0 to 1.0

5-State Classification:
    >= 0.75 → STRONGLY_BULLISH
    >= 0.60 → BULLISH
    >= 0.40 → NEUTRAL         (the band where V5 had no protection)
    >= 0.25 → BEARISH
    <  0.25 → STRONGLY_BEARISH

Empirical validation from 76-trade audit:
    BULLISH_FADING confirmed — EUR/USD declined while COT read BULLISH,
    penalizing correct SHORT trades by -15 points. The neutral band and
    5-state system directly addresses this by not rewarding/penalizing
    positions when institutional commitment is weak (NEUTRAL zone).
"""

import requests
import telebot
import logging
from datetime import datetime, timezone

from shared_functions import get_supabase_client, send_error_notification
from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    COT_LOOKBACK_WEEKS, COT_NEUTRAL_BAND, COT_STRONG_THRESHOLD,
    validate_config
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# CFTC Socrata API — TFF (Traders in Financial Futures) dataset
CFTC_API_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# CME market names for our pairs
MARKETS = {
    "EUR/USD": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
    "GBP/USD": "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE"
}


class COTTracker:
    """
    Tracks institutional positioning via CFTC Public Reporting API.
    Computes 52-week COT Index and classifies into 5 momentum states.
    """

    def __init__(self):
        try:
            validate_config('cot_tracker')
            self.supabase = get_supabase_client()
            self.bot = telebot.TeleBot(TELEGRAM_TOKEN)
            logger.info("[COT] Initialized successfully.")
        except Exception as e:
            logger.error(f"[COT] Initialization failed: {e}")
            send_error_notification(f"COT Tracker Init Failed: {e}")
            self.supabase = None
            self.bot = None

    # =========================================================================
    # SECTION 1: DATA FETCHING
    # =========================================================================

    def fetch_cot_history(self, market_name: str) -> list:
        """
        Fetches the last 52 weeks of COT data for a given market.

        Returns:
            List of dicts with keys: date, longs, shorts, net
            Sorted oldest → newest.
            Empty list on failure.
        """
        params = {
            "market_and_exchange_names": market_name,
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": COT_LOOKBACK_WEEKS
        }

        try:
            response = requests.get(CFTC_API_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            if not data:
                logger.warning(f"[COT] No data returned for {market_name}")
                return []

            history = []
            for record in data:
                try:
                    longs  = int(record.get('noncomm_positions_long_all',  0))
                    shorts = int(record.get('noncomm_positions_short_all', 0))
                    net    = longs - shorts
                    date   = record.get('report_date_as_yyyy_mm_dd', '')[:10]
                    history.append({
                        "date":   date,
                        "longs":  longs,
                        "shorts": shorts,
                        "net":    net
                    })
                except (ValueError, TypeError) as e:
                    # Skip malformed records, don't abort the whole fetch
                    logger.warning(f"[COT] Skipping malformed record: {e}")
                    continue

            # Reverse so list is oldest → newest
            history.reverse()
            logger.info(f"[COT] Fetched {len(history)} weeks of history for {market_name}")
            return history

        except Exception as e:
            logger.error(f"[COT] CFTC API error for {market_name}: {e}")
            send_error_notification(f"COT API Error ({market_name}): {e}")
            return []

    # =========================================================================
    # SECTION 2: 52-WEEK INDEX CALCULATION
    # =========================================================================

    def calculate_cot_index(self, history: list) -> dict:
        """
        Computes the COT Index using the industry-standard formula:
            index = (current_net - min_52w) / (max_52w - min_52w)

        This normalizes raw net positions to a 0.0–1.0 scale, making
        comparisons meaningful across different time periods and pairs.

        Args:
            history: List of weekly records (oldest → newest), each with 'net'

        Returns:
            Dict with:
                index:       float 0.0–1.0 (or None if insufficient data)
                current_net: int
                min_52w:     int
                max_52w:     int
                weeks_used:  int
                latest_date: str
        """
        if not history:
            return {"index": None, "current_net": 0, "min_52w": 0, "max_52w": 0,
                    "weeks_used": 0, "latest_date": "Unknown"}

        net_values  = [r['net'] for r in history]
        current_net = net_values[-1]   # Most recent week
        min_52w     = min(net_values)
        max_52w     = max(net_values)
        latest_date = history[-1]['date']

        # Avoid division by zero (flat market — all weeks identical)
        if max_52w == min_52w:
            index = 0.5  # Dead centre — genuinely neutral
            logger.warning("[COT] 52-week range is zero. Defaulting index to 0.5 (NEUTRAL).")
        else:
            index = (current_net - min_52w) / (max_52w - min_52w)

        # Clamp to [0.0, 1.0] for safety
        index = max(0.0, min(1.0, round(index, 4)))

        logger.info(
            f"[COT] Index: {index:.4f} | Net: {current_net:,} | "
            f"Range: [{min_52w:,} → {max_52w:,}] | Weeks: {len(history)}"
        )

        return {
            "index":       index,
            "current_net": current_net,
            "min_52w":     min_52w,
            "max_52w":     max_52w,
            "weeks_used":  len(history),
            "latest_date": latest_date
        }

    # =========================================================================
    # SECTION 3: 5-STATE CLASSIFICATION
    # =========================================================================

    def classify_bias(self, index: float) -> str:
        """
        Converts a COT Index value to one of 5 momentum states.

        Thresholds (from config.py):
            >= COT_STRONG_THRESHOLD (0.75) → STRONGLY_BULLISH
            >= (1 - COT_NEUTRAL_BAND) (0.60) → BULLISH
            >= COT_NEUTRAL_BAND (0.40)      → NEUTRAL
            >= (1 - COT_STRONG_THRESHOLD) (0.25) → BEARISH
            <  (1 - COT_STRONG_THRESHOLD)   → STRONGLY_BEARISH

        The NEUTRAL band (0.40–0.60) was the missing protection in V5.
        A net position of +1 contract was classified as BULLISH, causing
        the BULLISH_FADING problem confirmed in the 76-trade audit.

        Args:
            index: float 0.0–1.0 from calculate_cot_index()

        Returns:
            str: One of the 5 state strings
        """
        if index is None:
            return "NEUTRAL"

        if index >= COT_STRONG_THRESHOLD:
            return "STRONGLY_BULLISH"
        elif index >= (1.0 - COT_NEUTRAL_BAND):   # 0.60
            return "BULLISH"
        elif index >= COT_NEUTRAL_BAND:            # 0.40
            return "NEUTRAL"
        elif index >= (1.0 - COT_STRONG_THRESHOLD):  # 0.25
            return "BEARISH"
        else:
            return "STRONGLY_BEARISH"

    # =========================================================================
    # SECTION 4: DATABASE UPDATE
    # =========================================================================

    def update_system_state(self, pair: str, bias: str, cot_index: float,
                            current_net: int, latest_date: str):
        """
        Writes COT bias and index to system_state table in Supabase.
        Uses upsert on 'pair' — safe to run multiple times.
        """
        if not self.supabase:
            logger.error("[COT] Supabase client not available. Skipping DB update.")
            return

        valid_states = {
            "STRONGLY_BULLISH", "BULLISH", "NEUTRAL",
            "BEARISH", "STRONGLY_BEARISH"
        }
        if bias not in valid_states:
            logger.error(f"[COT] Invalid bias value '{bias}' for {pair}. Aborting update.")
            return

        try:
            self.supabase.table("system_state").upsert({
                "pair":         pair,
                "cot_bias":     bias,
                "cot_index":    cot_index,
                "cot_net":      current_net,
                "cot_date":     latest_date,
                "last_updated": datetime.now(timezone.utc).isoformat()
            }, on_conflict="pair").execute()

            logger.info(f"[COT] system_state updated → {pair}: {bias} (index: {cot_index:.4f})")

        except Exception as e:
            logger.error(f"[COT] Supabase update failed for {pair}: {e}")
            send_error_notification(f"COT Supabase Update Failed ({pair}): {e}")

    # =========================================================================
    # SECTION 5: TELEGRAM REPORT
    # =========================================================================

    def format_report(self, pair: str, bias: str, index_data: dict) -> str:
        """Formats a single pair's COT report for Telegram."""

        state_emoji = {
            "STRONGLY_BULLISH": "🟢🟢",
            "BULLISH":          "🟢",
            "NEUTRAL":          "⚪",
            "BEARISH":          "🔴",
            "STRONGLY_BEARISH": "🔴🔴"
        }

        emoji       = state_emoji.get(bias, "⚪")
        index_pct   = index_data['index'] * 100 if index_data['index'] is not None else 0
        index_bar   = self._build_index_bar(index_data['index'] or 0.5)

        report  = f"*{pair}* {emoji} {bias}\n"
        report += f"📊 COT Index: `{index_pct:.1f}%` {index_bar}\n"
        report += f"⚖️ Net Position: `{index_data['current_net']:,}`\n"
        report += f"📉 52W Range: `{index_data['min_52w']:,}` → `{index_data['max_52w']:,}`\n"
        report += f"📅 Report Date: `{index_data['latest_date']}`\n"
        report += f"📈 Weeks of Data: `{index_data['weeks_used']}`\n"

        return report

    @staticmethod
    def _build_index_bar(index: float, width: int = 10) -> str:
        """Builds a simple ASCII progress bar for the COT index."""
        filled = round(index * width)
        bar    = "█" * filled + "░" * (width - filled)
        return f"`[{bar}]`"

    # =========================================================================
    # SECTION 6: MAIN RUN LOOP
    # =========================================================================

    def run(self):
        """
        Main execution:
        1. Fetch 52 weeks of CFTC data for each pair
        2. Calculate COT Index
        3. Classify into 5-state bias
        4. Update Supabase system_state
        5. Send Telegram report
        """
        logger.info("[COT] ===== COT Tracker Starting =====")

        full_report  = "🏦 *SMART MONEY TRACKER (COT)* 🏦\n"
        full_report += "_52-Week Index Normalization — 5-State Classification_\n\n"

        any_success = False

        for pair, market_name in MARKETS.items():
            logger.info(f"[COT] Processing {pair}...")

            # Step 1: Fetch history
            history = self.fetch_cot_history(market_name)

            if not history:
                full_report += f"⚠️ *{pair}:* Data unavailable from CFTC API\n\n"
                continue

            # Step 2: Calculate index
            index_data = self.calculate_cot_index(history)

            if index_data['index'] is None:
                full_report += f"⚠️ *{pair}:* Index calculation failed\n\n"
                continue

            # Step 3: Classify
            bias = self.classify_bias(index_data['index'])
            logger.info(f"[COT] {pair} → {bias} (index: {index_data['index']:.4f})")

            # Step 4: Write to DB
            self.update_system_state(
                pair        = pair,
                bias        = bias,
                cot_index   = index_data['index'],
                current_net = index_data['current_net'],
                latest_date = index_data['latest_date']
            )

            # Step 5: Build report section
            full_report += self.format_report(pair, bias, index_data)
            full_report += "\n"
            any_success  = True

        # Send Telegram report
        if self.bot:
            try:
                if any_success:
                    self.bot.send_message(
                        TELEGRAM_CHAT_ID,
                        full_report,
                        parse_mode="Markdown",
                        timeout=10
                    )
                    logger.info("[COT] Telegram report sent.")
                else:
                    self.bot.send_message(
                        TELEGRAM_CHAT_ID,
                        "⚠️ COT Tracker: Failed to fetch data for all pairs. Check CFTC API.",
                        timeout=10
                    )
            except Exception as e:
                logger.error(f"[COT] Telegram send failed: {e}")
                send_error_notification(f"COT Telegram Report Failed: {e}")

        logger.info("[COT] ===== COT Tracker Complete =====")


if __name__ == "__main__":
    tracker = COTTracker()
    tracker.run()
