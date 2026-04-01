"""
Supabase Real-Time Monitoring Dashboard
Displays live trade logs, sentiment data, and system state.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from tabulate import tabulate

from config import SUPABASE_URL, SUPABASE_KEY
from shared_functions import get_supabase_client

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SupabaseMonitor:
    """
    Real-time monitoring of Supabase data.
    """
    
    def __init__(self):
        self.supabase = get_supabase_client()
    
    def display_system_state(self):
        """Displays current system state for each pair."""
        logger.info("=" * 80)
        logger.info("SYSTEM STATE")
        logger.info("=" * 80)
        
        try:
            response = self.supabase.table("system_state").select("*").execute()
            
            if response.data:
                table_data = []
                for state in response.data:
                    table_data.append([
                        state.get('pair', 'N/A'),
                        state.get('macro_sentiment', 0),
                        state.get('cot_bias', 'NEUTRAL'),
                        state.get('last_updated', 'N/A')
                    ])
                
                print(tabulate(table_data, headers=['Pair', 'Macro Sentiment', 'COT Bias', 'Last Updated'], tablefmt='grid'))
            else:
                print("No system state data found")
        except Exception as e:
            logger.error(f"Error fetching system state: {e}")
    
    def display_recent_trades(self, hours: int = 24, limit: int = 20):
        """Displays recent trades."""
        logger.info("=" * 80)
        logger.info(f"RECENT TRADES (Last {hours} hours)")
        logger.info("=" * 80)
        
        try:
            now = datetime.now(timezone.utc)
            time_threshold = now - timedelta(hours=hours)
            
            response = self.supabase.table("trade_logs").select("*").gte(
                "timestamp_ist", time_threshold.isoformat()
            ).order("timestamp_ist", desc=True).limit(limit).execute()
            
            if response.data:
                table_data = []
                for trade in response.data:
                    table_data.append([
                        trade.get('timestamp_ist', 'N/A')[:19],
                        trade.get('pair', 'N/A'),
                        trade.get('direction', 'N/A'),
                        trade.get('confidence_score', 'N/A'),
                        trade.get('entry_price', 'N/A'),
                        trade.get('exit_price', 'N/A'),
                        trade.get('pips', 'N/A'),
                        trade.get('result', 'PENDING')
                    ])
                
                print(tabulate(table_data, headers=[
                    'Timestamp', 'Pair', 'Direction', 'Score', 'Entry', 'Exit', 'Pips', 'Result'
                ], tablefmt='grid'))
                
                # Statistics
                total = len(response.data)
                wins = sum(1 for t in response.data if t.get('result') == 'WIN')
                losses = sum(1 for t in response.data if t.get('result') == 'LOSS')
                pending = sum(1 for t in response.data if t.get('result') not in ['WIN', 'LOSS'])
                
                print(f"\n📊 Statistics:")
                print(f"  Total Trades: {total}")
                print(f"  Wins: {wins}")
                print(f"  Losses: {losses}")
                print(f"  Pending: {pending}")
                if (wins + losses) > 0:
                    win_rate = (wins / (wins + losses)) * 100
                    print(f"  Win Rate: {win_rate:.1f}%")
            else:
                print("No recent trades found")
        except Exception as e:
            logger.error(f"Error fetching trades: {e}")
    
    def display_sentiment_summary(self, hours: int = 24):
        """Displays sentiment analysis summary."""
        logger.info("=" * 80)
        logger.info(f"SENTIMENT ANALYSIS (Last {hours} hours)")
        logger.info("=" * 80)
        
        try:
            now = datetime.now(timezone.utc)
            time_threshold = now - timedelta(hours=hours)
            
            response = self.supabase.table("processed_sentiment").select("*").gte(
                "created_at", time_threshold.isoformat()
            ).order("created_at", desc=True).execute()
            
            if response.data:
                # EUR/USD Sentiment
                eur_bullish = sum(1 for s in response.data if s.get('eur_usd_sentiment') == 'BULLISH')
                eur_bearish = sum(1 for s in response.data if s.get('eur_usd_sentiment') == 'BEARISH')
                eur_neutral = sum(1 for s in response.data if s.get('eur_usd_sentiment') == 'NEUTRAL')
                
                # GBP/USD Sentiment
                gbp_bullish = sum(1 for s in response.data if s.get('gbp_usd_sentiment') == 'BULLISH')
                gbp_bearish = sum(1 for s in response.data if s.get('gbp_usd_sentiment') == 'BEARISH')
                gbp_neutral = sum(1 for s in response.data if s.get('gbp_usd_sentiment') == 'NEUTRAL')
                
                # Importance breakdown
                high_importance = sum(1 for s in response.data if s.get('importance_tier') == 'HIGH')
                medium_importance = sum(1 for s in response.data if s.get('importance_tier') == 'MEDIUM')
                low_importance = sum(1 for s in response.data if s.get('importance_tier') == 'LOW')
                
                # Source breakdown
                news_count = sum(1 for s in response.data if s.get('source') == 'news')
                twitter_count = sum(1 for s in response.data if s.get('source') == 'twitter')
                
                print(f"\n📈 EUR/USD Sentiment:")
                print(f"  Bullish: {eur_bullish}")
                print(f"  Bearish: {eur_bearish}")
                print(f"  Neutral: {eur_neutral}")
                
                print(f"\n📈 GBP/USD Sentiment:")
                print(f"  Bullish: {gbp_bullish}")
                print(f"  Bearish: {gbp_bearish}")
                print(f"  Neutral: {gbp_neutral}")
                
                print(f"\n⭐ Importance Distribution:")
                print(f"  HIGH: {high_importance}")
                print(f"  MEDIUM: {medium_importance}")
                print(f"  LOW: {low_importance}")
                
                print(f"\n📰 Source Distribution:")
                print(f"  News: {news_count}")
                print(f"  Twitter: {twitter_count}")
                
                # Recent sentiments table
                print(f"\n📋 Recent Sentiment Signals (Top 10):")
                table_data = []
                for sentiment in response.data[:10]:
                    table_data.append([
                        sentiment.get('created_at', 'N/A')[:19],
                        sentiment.get('source', 'N/A'),
                        sentiment.get('importance_tier', 'N/A'),
                        sentiment.get('eur_usd_sentiment', 'N/A'),
                        f"{sentiment.get('eur_usd_confidence', 0):.2f}",
                        sentiment.get('gbp_usd_sentiment', 'N/A'),
                        f"{sentiment.get('gbp_usd_confidence', 0):.2f}",
                        sentiment.get('model_used', 'N/A')
                    ])
                
                print(tabulate(table_data, headers=[
                    'Timestamp', 'Source', 'Importance', 'EUR Sent', 'EUR Conf', 'GBP Sent', 'GBP Conf', 'Model'
                ], tablefmt='grid'))
            else:
                print("No sentiment data found")
        except Exception as e:
            logger.error(f"Error fetching sentiment data: {e}")
    
    def display_raw_sentiment_count(self):
        """Displays raw sentiment data collection stats."""
        logger.info("=" * 80)
        logger.info("RAW SENTIMENT DATA COLLECTION")
        logger.info("=" * 80)
        
        try:
            response = self.supabase.table("raw_sentiment_data").select("*", count="exact").execute()
            total = len(response.data) if response.data else 0
            
            # By source
            if response.data:
                news_count = sum(1 for s in response.data if s.get('source') == 'news')
                twitter_count = sum(1 for s in response.data if s.get('source') == 'twitter')
            else:
                news_count = twitter_count = 0
            
            print(f"\n📊 Total Raw Items Collected: {total}")
            print(f"  News: {news_count}")
            print(f"  Twitter: {twitter_count}")
        except Exception as e:
            logger.error(f"Error fetching raw sentiment count: {e}")
    
    def run_full_monitor(self):
        """Runs the complete monitoring dashboard."""
        logger.info("=" * 80)
        logger.info("SUPABASE REAL-TIME MONITORING DASHBOARD")
        logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
        logger.info("=" * 80)
        
        self.display_system_state()
        print("\n")
        self.display_recent_trades(hours=24, limit=20)
        print("\n")
        self.display_sentiment_summary(hours=24)
        print("\n")
        self.display_raw_sentiment_count()
        
        logger.info("=" * 80)
        logger.info("END OF MONITORING REPORT")
        logger.info("=" * 80)


def main():
    """Main entry point."""
    monitor = SupabaseMonitor()
    monitor.run_full_monitor()


if __name__ == "__main__":
    main()
