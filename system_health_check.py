"""
system_health_check.py - System Diagnostics & Health Monitor
Fusion Score Bot V6.0

Runs on a schedule via GitHub Actions (health.yml).
Checks all system components and reports to Telegram via the Error Bot.

Always sends a message:
    - All OK  → brief green summary to Error Bot
    - Any fail → detailed red alert listing every failed check

Key fixes from V5 audit:
    - Dict imported from typing (was NameError crash on startup)
    - validate_config() now raises — health check catches EnvironmentError properly
    - Supabase count uses .count property not len(data) (fixes pagination undercount)
    - Gemini API checked with POST not HEAD (HEAD returns nothing meaningful)
    - API key validation actually tests functionality, not just reachability
    - No workflow existed in V5 — health.yml will be added
    - Sends to Error Bot (separate channel from trade signals)
"""

import json
import logging
import requests
from datetime import datetime, timezone, timedelta
# typing not needed — using Python 3.10 built-in dict/list/tuple annotations

import telebot

from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    ERROR_BOT_TOKEN, ERROR_CHAT_ID,
    TWELVE_DATA_KEY, GEMINI_API_KEY,
    GNEWS_API_KEY, SUPABASE_URL, SUPABASE_KEY,
    validate_config
)
from shared_functions import get_supabase_client, send_error_notification

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Required Supabase tables
REQUIRED_TABLES = [
    "system_state",
    "trade_logs",
    "raw_sentiment_data",
    "processed_sentiment"
]


# =============================================================================
# TELEGRAM REPORTER
# Sends to Error Bot if configured, falls back to main bot.
# =============================================================================

def _get_report_bot() -> tuple:
    """
    Returns (bot, chat_id) using Error Bot if available,
    falling back to main bot.
    """
    token   = ERROR_BOT_TOKEN if ERROR_BOT_TOKEN else TELEGRAM_TOKEN
    chat_id = ERROR_CHAT_ID   if ERROR_CHAT_ID   else TELEGRAM_CHAT_ID
    return telebot.TeleBot(token), chat_id


def send_health_report(report: dict):
    """
    Sends the health report to Telegram.
    Always sends — OK summary or failure alert.
    """
    try:
        bot, chat_id = _get_report_bot()

        alerts  = report.get("alerts", [])
        passed  = report.get("passed", 0)
        total   = report.get("total",  0)
        all_ok  = len(alerts) == 0

        if all_ok:
            msg  = "✅ *FUSION BOT — DAILY HEALTH CHECK*\n\n"
            msg += f"All `{total}` checks passed.\n\n"

            # Brief stats from DB
            db = report.get("database", {})

            trades = db.get("trade_logging", {})
            msg += f"📊 Total trades: `{trades.get('total_trades', 'N/A')}`\n"
            msg += f"📊 Trades (24h): `{trades.get('recent_24h', 'N/A')}`\n"
            msg += f"📊 Win rate: `{trades.get('win_rate', 'N/A')}`\n\n"

            sentiment = db.get("sentiment_data", {})
            msg += f"🧠 Sentiment records (24h): `{sentiment.get('recent_24h', 'N/A')}`\n\n"

            # System state
            state = db.get("system_state", {})
            for pair_data in state.get("pairs", []):
                pair      = pair_data.get('pair', '?')
                sentiment_val = pair_data.get('macro_sentiment', 0)
                cot       = pair_data.get('cot_bias', 'N/A')
                cot_idx   = pair_data.get('cot_index')
                idx_str   = f"{cot_idx:.3f}" if cot_idx is not None else "N/A"
                msg += f"💹 {pair}: Sentiment `{sentiment_val:+d}` | COT `{cot}` (idx: `{idx_str}`)\n"

            msg += f"\n_Check time: {report['timestamp']}_"

        else:
            msg  = "🚨 *FUSION BOT — HEALTH ALERT* 🚨\n\n"
            msg += f"`{passed}/{total}` checks passed.\n\n"
            msg += "*Failed checks:*\n"

            for alert in alerts:
                msg += f"❌ {alert}\n"

            msg += f"\n_Check time: {report['timestamp']}_"

        bot.send_message(chat_id, msg, timeout=15)
        logger.info(f"[Health] Report sent to Telegram ({'OK' if all_ok else 'ALERT'}).")

    except Exception as e:
        logger.error(f"[Health] Failed to send Telegram report: {e}")


# =============================================================================
# HEALTH CHECK CLASS
# =============================================================================

class SystemHealthCheck:
    """
    Runs all system health checks and compiles a report.
    """

    def __init__(self):
        self.report = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "checks":    {},
            "database":  {},
            "alerts":    [],
            "passed":    0,
            "total":     0
        }

    def _record(self, name: str, passed: bool, message: str, detail: dict = None):
        """Records a check result."""
        self.report["total"] += 1
        if passed:
            self.report["passed"] += 1

        self.report["checks"][name] = {
            "status":  "✅ PASS" if passed else "❌ FAIL",
            "message": message,
            **(detail or {})
        }

        if not passed:
            self.report["alerts"].append(f"{name}: {message}")

        status_str = "PASS" if passed else "FAIL"
        logger.info(f"[Health] {name}: {status_str} — {message}")

    # =========================================================================
    # CHECK 1: Environment Variables
    # =========================================================================

    def check_environment(self) -> bool:
        """Verifies all required environment variables are set."""
        logger.info("[Health] Checking environment variables...")

        try:
            # validate_config now raises EnvironmentError on missing vars
            validate_config()
            self._record("environment", True, "All required env vars present.")
            return True
        except EnvironmentError as e:
            self._record("environment", False, str(e))
            return False

    # =========================================================================
    # CHECK 2: Supabase Connection
    # =========================================================================

    def check_supabase_connection(self) -> bool:
        """Verifies Supabase client connects and responds."""
        logger.info("[Health] Checking Supabase connection...")

        try:
            supabase = get_supabase_client()
            # Lightweight query to confirm connection
            supabase.table("system_state").select("pair").limit(1).execute()
            self._record("supabase_connection", True, "Connected successfully.")
            return True
        except Exception as e:
            self._record("supabase_connection", False, str(e))
            return False

    # =========================================================================
    # CHECK 3: Supabase Tables
    # =========================================================================

    def check_supabase_tables(self) -> bool:
        """Verifies all required tables exist and are queryable."""
        logger.info("[Health] Checking Supabase tables...")

        try:
            supabase    = get_supabase_client()
            missing     = []
            found       = []

            for table in REQUIRED_TABLES:
                try:
                    supabase.table(table).select("*").limit(1).execute()
                    found.append(table)
                except Exception:
                    missing.append(table)

            if missing:
                self._record(
                    "supabase_tables", False,
                    f"Missing tables: {', '.join(missing)}",
                    {"found": found, "missing": missing}
                )
                return False

            self._record(
                "supabase_tables", True,
                f"All {len(REQUIRED_TABLES)} tables exist.",
                {"tables": found}
            )
            return True

        except Exception as e:
            self._record("supabase_tables", False, str(e))
            return False

    # =========================================================================
    # CHECK 4: API Connectivity
    # V5 bug: used requests.HEAD for Gemini which tells you nothing.
    # Now uses a real minimal POST to verify the key actually works.
    # TwelveData: real GET request (costs 1 credit — acceptable for health check).
    # GNews: real GET request with minimal result.
    # =========================================================================

    def check_api_connectivity(self) -> bool:
        """Tests that external APIs are reachable and keys are valid."""
        logger.info("[Health] Checking API connectivity...")

        api_status = {}
        all_ok     = True

        # --- TwelveData ---
        try:
            if TWELVE_DATA_KEY:
                url      = (
                    f"https://api.twelvedata.com/time_series"
                    f"?symbol=EUR/USD&interval=1min&outputsize=1"
                    f"&apikey={TWELVE_DATA_KEY}"
                )
                response = requests.get(url, timeout=10)
                data     = response.json()

                if data.get('status') == 'error':
                    api_status["TwelveData"] = f"❌ API error: {data.get('message')}"
                    all_ok = False
                else:
                    api_status["TwelveData"] = "✅ OK"
            else:
                api_status["TwelveData"] = "⚠️ Key not set"
                all_ok = False
        except Exception as e:
            api_status["TwelveData"] = f"❌ {str(e)[:60]}"
            all_ok = False

        # --- Gemini (minimal POST to verify key) ---
        try:
            if GEMINI_API_KEY:
                url     = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
                )
                payload = {"contents": [{"parts": [{"text": "Hi"}]}]}
                response = requests.post(url, json=payload, timeout=10)

                if response.status_code == 200:
                    api_status["Gemini"] = "✅ OK"
                elif response.status_code == 429:
                    # Rate limited but key is valid
                    api_status["Gemini"] = "⚠️ Rate limited (key valid)"
                else:
                    api_status["Gemini"] = f"❌ HTTP {response.status_code}"
                    all_ok = False
            else:
                api_status["Gemini"] = "⚠️ Key not set"
                all_ok = False
        except Exception as e:
            api_status["Gemini"] = f"❌ {str(e)[:60]}"
            all_ok = False

        # --- GNews ---
        try:
            if GNEWS_API_KEY:
                url      = (
                    f"https://gnews.io/api/v4/search"
                    f"?q=forex&token={GNEWS_API_KEY}&lang=en&max=1"
                )
                response = requests.get(url, timeout=10)
                data     = response.json()

                if 'errors' in data:
                    api_status["GNews"] = f"❌ {data['errors']}"
                    all_ok = False
                else:
                    api_status["GNews"] = "✅ OK"
            else:
                api_status["GNews"] = "⚠️ Key not set"
                all_ok = False
        except Exception as e:
            api_status["GNews"] = f"❌ {str(e)[:60]}"
            all_ok = False

        self._record(
            "api_connectivity",
            all_ok,
            "All APIs reachable." if all_ok else "One or more APIs failed.",
            {"apis": api_status}
        )
        return all_ok

    # =========================================================================
    # CHECK 5: Trade Logging
    # V5 bug: used len(data) for count — misses paginated rows beyond 1000.
    # Now uses count="exact" and reads .count property.
    # =========================================================================

    def check_trade_logging(self):
        """Verifies trades are being logged and computes win rate."""
        logger.info("[Health] Checking trade logging...")

        try:
            supabase = get_supabase_client()
            now      = datetime.now(timezone.utc)
            yesterday = now - timedelta(hours=24)

            # Total count — use .count not len(data)
            total_resp  = (
                supabase.table("trade_logs")
                .select("*", count="exact")
                .execute()
            )
            total_trades = total_resp.count or 0

            # Recent 24h
            recent_resp = (
                supabase.table("trade_logs")
                .select("*", count="exact")
                .gte("timestamp_ist", yesterday.isoformat())
                .execute()
            )
            recent_count = recent_resp.count or 0

            # Win/Loss/Breakeven from recent data (fetch data for this calc)
            all_resp = supabase.table("trade_logs").select("result").execute()
            all_data = all_resp.data or []

            wins      = sum(1 for t in all_data if t.get('result') == 'WIN')
            losses    = sum(1 for t in all_data if t.get('result') == 'LOSS')
            breakeven = sum(1 for t in all_data if t.get('result') == 'BREAKEVEN')
            decided   = wins + losses + breakeven
            win_rate  = f"{(wins / decided * 100):.1f}%" if decided > 0 else "N/A"

            db_entry = {
                "total_trades": total_trades,
                "recent_24h":   recent_count,
                "wins":         wins,
                "losses":       losses,
                "breakeven":    breakeven,
                "win_rate":     win_rate
            }
            self.report["database"]["trade_logging"] = db_entry

            self._record(
                "trade_logging",
                True,
                f"{total_trades} total trades. Win rate: {win_rate}",
                db_entry
            )

        except Exception as e:
            self._record("trade_logging", False, str(e))
            self.report["database"]["trade_logging"] = {"error": str(e)}

    # =========================================================================
    # CHECK 6: Sentiment Data
    # =========================================================================

    def check_sentiment_data(self):
        """Verifies sentiment records are being stored."""
        logger.info("[Health] Checking sentiment data...")

        try:
            supabase  = get_supabase_client()
            now       = datetime.now(timezone.utc)
            yesterday = now - timedelta(hours=24)

            total_resp = (
                supabase.table("processed_sentiment")
                .select("*", count="exact")
                .execute()
            )
            total = total_resp.count or 0

            recent_resp = (
                supabase.table("processed_sentiment")
                .select("*", count="exact")
                .gte("created_at", yesterday.isoformat())
                .execute()
            )
            recent = recent_resp.count or 0

            db_entry = {
                "total_records": total,
                "recent_24h":    recent
            }
            self.report["database"]["sentiment_data"] = db_entry

            # Warn if no sentiment in last 24h (pipeline may be broken)
            ok = recent > 0 or total == 0  # OK if fresh install with no data yet
            self._record(
                "sentiment_data",
                ok,
                f"{total} total records. {recent} in last 24h.",
                db_entry
            )

        except Exception as e:
            self._record("sentiment_data", False, str(e))
            self.report["database"]["sentiment_data"] = {"error": str(e)}

    # =========================================================================
    # CHECK 7: System State (Fusion Score inputs)
    # =========================================================================

    def check_system_state(self):
        """
        Reads system_state for both pairs.
        Flags if macro_sentiment is still 0 (sentinel pipeline may be broken)
        or if COT data is stale (older than 8 days).
        """
        logger.info("[Health] Checking system state...")

        try:
            supabase  = get_supabase_client()
            response  = supabase.table("system_state").select("*").execute()
            pairs_data = response.data or []

            self.report["database"]["system_state"] = {"pairs": pairs_data}

            warnings = []
            for row in pairs_data:
                pair      = row.get('pair', '?')
                sentiment = row.get('macro_sentiment', 0)
                cot       = row.get('cot_bias', 'NEUTRAL')
                cot_date  = row.get('cot_date')

                if sentiment == 0:
                    warnings.append(
                        f"{pair}: macro_sentiment=0 "
                        f"(sentiment pipeline may not be aggregating)"
                    )

                if cot_date:
                    try:
                        from datetime import date
                        cot_dt   = date.fromisoformat(cot_date)
                        age_days = (date.today() - cot_dt).days
                        if age_days > 8:
                            warnings.append(
                                f"{pair}: COT data is {age_days} days old "
                                f"(last: {cot_date})"
                            )
                    except ValueError:
                        pass

            if warnings:
                self._record(
                    "system_state", False,
                    " | ".join(warnings),
                    {"pairs": pairs_data}
                )
            else:
                self._record(
                    "system_state", True,
                    "All pairs have fresh COT and non-zero sentiment.",
                    {"pairs": pairs_data}
                )

        except Exception as e:
            self._record("system_state", False, str(e))

    # =========================================================================
    # MAIN RUNNER
    # =========================================================================

    def run(self) -> dict:
        """Runs all checks and sends Telegram report."""
        logger.info("[Health] ===== Health Check Starting =====")

        self.check_environment()
        self.check_supabase_connection()
        self.check_supabase_tables()
        self.check_api_connectivity()
        self.check_trade_logging()
        self.check_sentiment_data()
        self.check_system_state()

        alerts  = self.report["alerts"]
        passed  = self.report["passed"]
        total   = self.report["total"]

        if alerts:
            logger.warning(f"[Health] {len(alerts)} alert(s): {alerts}")
        else:
            logger.info(f"[Health] All {total} checks passed.")

        logger.info("[Health] ===== Health Check Complete =====")

        # Always send to Telegram
        send_health_report(self.report)

        return self.report


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    checker = SystemHealthCheck()
    report  = checker.run()
    # Also print JSON for GitHub Actions log
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
