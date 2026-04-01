"""
System Health Check & Diagnostic Tool
Verifies all components are working correctly and Supabase is logging trades.
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
import requests

from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TWELVE_DATA_KEY, GEMINI_API_KEY,
    GNEWS_API_KEY, NEWS_API_KEY, SUPABASE_URL, SUPABASE_KEY, validate_config
)
from shared_functions import get_supabase_client

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SystemHealthCheck:
    """
    Comprehensive system diagnostics and health verification.
    """
    
    def __init__(self):
        self.health_report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {},
            "database": {},
            "alerts": []
        }
    
    def check_environment_variables(self) -> bool:
        """Verifies all required environment variables are set."""
        logger.info("[Health] Checking environment variables...")
        
        try:
            validate_config()
            self.health_report["checks"]["environment"] = {
                "status": "✅ PASS",
                "message": "All required environment variables are set"
            }
            return True
        except EnvironmentError as e:
            self.health_report["checks"]["environment"] = {
                "status": "❌ FAIL",
                "message": str(e)
            }
            self.health_report["alerts"].append(f"Environment: {e}")
            return False
    
    def check_supabase_connection(self) -> bool:
        """Verifies Supabase connection."""
        logger.info("[Health] Checking Supabase connection...")
        
        try:
            supabase = get_supabase_client()
            # Test query
            response = supabase.table("system_state").select("*").limit(1).execute()
            
            self.health_report["checks"]["supabase_connection"] = {
                "status": "✅ PASS",
                "message": "Connected to Supabase successfully"
            }
            return True
        except Exception as e:
            self.health_report["checks"]["supabase_connection"] = {
                "status": "❌ FAIL",
                "message": str(e)
            }
            self.health_report["alerts"].append(f"Supabase Connection: {e}")
            return False
    
    def check_supabase_tables(self) -> bool:
        """Verifies all required Supabase tables exist."""
        logger.info("[Health] Checking Supabase tables...")
        
        required_tables = [
            "system_state",
            "trade_logs",
            "raw_sentiment_data",
            "processed_sentiment"
        ]
        
        try:
            supabase = get_supabase_client()
            all_exist = True
            
            for table in required_tables:
                try:
                    response = supabase.table(table).select("*").limit(1).execute()
                    logger.info(f"  ✅ Table '{table}' exists")
                except Exception as e:
                    logger.warning(f"  ⚠️ Table '{table}' may not exist: {e}")
                    all_exist = False
            
            self.health_report["checks"]["supabase_tables"] = {
                "status": "✅ PASS" if all_exist else "⚠️ WARNING",
                "message": f"Checked {len(required_tables)} required tables",
                "tables": required_tables
            }
            return all_exist
        except Exception as e:
            self.health_report["checks"]["supabase_tables"] = {
                "status": "❌ FAIL",
                "message": str(e)
            }
            self.health_report["alerts"].append(f"Supabase Tables: {e}")
            return False
    
    def check_api_connectivity(self) -> bool:
        """Tests connectivity to external APIs."""
        logger.info("[Health] Checking API connectivity...")
        
        api_checks = {
            "Twelve Data": f"https://api.twelvedata.com/time_series?symbol=EUR/USD&interval=1min&apikey={TWELVE_DATA_KEY}",
            "Gemini": f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
            "GNews": f"https://gnews.io/api/v4/search?q=test&token={GNEWS_API_KEY}&lang=en&max=1"
        }
        
        api_status = {}
        all_ok = True
        
        for api_name, url in api_checks.items():
            try:
                response = requests.head(url, timeout=5)
                if response.status_code < 500:
                    api_status[api_name] = "✅ PASS"
                    logger.info(f"  ✅ {api_name} is reachable")
                else:
                    api_status[api_name] = "⚠️ WARNING (Server Error)"
                    all_ok = False
            except requests.exceptions.Timeout:
                api_status[api_name] = "⚠️ TIMEOUT"
                all_ok = False
            except Exception as e:
                api_status[api_name] = f"❌ FAIL ({str(e)[:50]})"
                all_ok = False
        
        self.health_report["checks"]["api_connectivity"] = {
            "status": "✅ PASS" if all_ok else "⚠️ WARNING",
            "apis": api_status
        }
        return all_ok
    
    def check_trade_logging(self) -> Dict:
        """Verifies that trades are being logged to Supabase."""
        logger.info("[Health] Checking trade logging...")
        
        try:
            supabase = get_supabase_client()
            
            # Get total trade count
            all_trades = supabase.table("trade_logs").select("*", count="exact").execute()
            total_trades = len(all_trades.data) if all_trades.data else 0
            
            # Get recent trades (last 24 hours)
            now = datetime.now(timezone.utc)
            yesterday = now - timedelta(hours=24)
            
            recent_trades = supabase.table("trade_logs").select("*").gte(
                "timestamp_ist", yesterday.isoformat()
            ).execute()
            recent_count = len(recent_trades.data) if recent_trades.data else 0
            
            # Get trades by pair
            pair_stats = {}
            for pair in ['EUR/USD', 'GBP/USD']:
                pair_trades = supabase.table("trade_logs").select("*").eq("pair", pair).execute()
                pair_stats[pair] = len(pair_trades.data) if pair_trades.data else 0
            
            # Get win/loss ratio
            all_trades_data = supabase.table("trade_logs").select("*").execute()
            if all_trades_data.data:
                wins = sum(1 for t in all_trades_data.data if t.get('result') == 'WIN')
                losses = sum(1 for t in all_trades_data.data if t.get('result') == 'LOSS')
                win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
            else:
                wins = losses = win_rate = 0
            
            self.health_report["database"]["trade_logging"] = {
                "status": "✅ PASS" if total_trades > 0 else "⚠️ NO TRADES YET",
                "total_trades": total_trades,
                "recent_trades_24h": recent_count,
                "by_pair": pair_stats,
                "win_rate": f"{win_rate:.1f}%" if (wins + losses) > 0 else "N/A",
                "wins": wins,
                "losses": losses
            }
            
            logger.info(f"  Total trades: {total_trades}")
            logger.info(f"  Recent trades (24h): {recent_count}")
            logger.info(f"  By pair: {pair_stats}")
            logger.info(f"  Win rate: {win_rate:.1f}%")
            
            return self.health_report["database"]["trade_logging"]
            
        except Exception as e:
            self.health_report["database"]["trade_logging"] = {
                "status": "❌ FAIL",
                "error": str(e)
            }
            self.health_report["alerts"].append(f"Trade Logging: {e}")
            return self.health_report["database"]["trade_logging"]
    
    def check_sentiment_data(self) -> Dict:
        """Verifies sentiment data is being stored."""
        logger.info("[Health] Checking sentiment data...")
        
        try:
            supabase = get_supabase_client()
            
            # Get total sentiment records
            all_sentiment = supabase.table("processed_sentiment").select("*", count="exact").execute()
            total_sentiment = len(all_sentiment.data) if all_sentiment.data else 0
            
            # Get recent sentiment (last 24 hours)
            now = datetime.now(timezone.utc)
            yesterday = now - timedelta(hours=24)
            
            recent_sentiment = supabase.table("processed_sentiment").select("*").gte(
                "created_at", yesterday.isoformat()
            ).execute()
            recent_count = len(recent_sentiment.data) if recent_sentiment.data else 0
            
            # Get sentiment breakdown
            if all_sentiment.data:
                bullish_eur = sum(1 for s in all_sentiment.data if s.get('eur_usd_sentiment') == 'BULLISH')
                bearish_eur = sum(1 for s in all_sentiment.data if s.get('eur_usd_sentiment') == 'BEARISH')
                bullish_gbp = sum(1 for s in all_sentiment.data if s.get('gbp_usd_sentiment') == 'BULLISH')
                bearish_gbp = sum(1 for s in all_sentiment.data if s.get('gbp_usd_sentiment') == 'BEARISH')
            else:
                bullish_eur = bearish_eur = bullish_gbp = bearish_gbp = 0
            
            self.health_report["database"]["sentiment_data"] = {
                "status": "✅ PASS" if total_sentiment > 0 else "⚠️ NO SENTIMENT DATA YET",
                "total_records": total_sentiment,
                "recent_24h": recent_count,
                "eur_usd": {
                    "bullish": bullish_eur,
                    "bearish": bearish_eur
                },
                "gbp_usd": {
                    "bullish": bullish_gbp,
                    "bearish": bearish_gbp
                }
            }
            
            logger.info(f"  Total sentiment records: {total_sentiment}")
            logger.info(f"  Recent (24h): {recent_count}")
            logger.info(f"  EUR/USD: {bullish_eur} bullish, {bearish_eur} bearish")
            logger.info(f"  GBP/USD: {bullish_gbp} bullish, {bearish_gbp} bearish")
            
            return self.health_report["database"]["sentiment_data"]
            
        except Exception as e:
            self.health_report["database"]["sentiment_data"] = {
                "status": "❌ FAIL",
                "error": str(e)
            }
            self.health_report["alerts"].append(f"Sentiment Data: {e}")
            return self.health_report["database"]["sentiment_data"]
    
    def run_full_health_check(self) -> Dict:
        """Runs all health checks."""
        logger.info("=" * 60)
        logger.info("SYSTEM HEALTH CHECK INITIATED")
        logger.info("=" * 60)
        
        # Run all checks
        self.check_environment_variables()
        self.check_supabase_connection()
        self.check_supabase_tables()
        self.check_api_connectivity()
        self.check_trade_logging()
        self.check_sentiment_data()
        
        # Summary
        logger.info("=" * 60)
        logger.info("HEALTH CHECK COMPLETE")
        logger.info("=" * 60)
        
        if self.health_report["alerts"]:
            logger.warning(f"⚠️ {len(self.health_report['alerts'])} alerts found:")
            for alert in self.health_report["alerts"]:
                logger.warning(f"  - {alert}")
        else:
            logger.info("✅ All systems operational!")
        
        return self.health_report


def main():
    """Main entry point."""
    health_check = SystemHealthCheck()
    report = health_check.run_full_health_check()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
