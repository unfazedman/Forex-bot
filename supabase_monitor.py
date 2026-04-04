"""
supabase_monitor.py - Real-Time Monitoring Dashboard
Fusion Score Bot V6.0

Run manually from command line to inspect live Supabase data.
Not scheduled — purely a diagnostic/inspection tool.

Usage:
    python supabase_monitor.py

Key fixes from V5 audit:
    - __init__ has proper error handling (no unhandled crash on bad credentials)
    - Twitter/snscrape source counters removed (never implemented, always 0)
    - Trade fetch uses .count for totals, not len(data)
    - display_recent_trades fetches with pagination limit to avoid memory issues
    - display_system_state shows new V6 fields: cot_index, cot_net, cot_date
    - All display methods have individual try/except — one failure doesn't
      abort the whole dashboard
"""

import logging
from datetime import datetime, timezone, timedelta

from tabulate import tabulate

from config import validate_config
from shared_functions import get_supabase_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Max rows to fetch for display (prevents memory issues as table grows)
DISPLAY_LIMIT = 25


class SupabaseMonitor:
    """
    Real-time monitoring dashboard for Supabase data.
    Displays system state, recent trades, and sentiment summary.
    """

    def __init__(self):
        try:
            validate_config()
            self.supabase = get_supabase_client()
            logger.info("[Monitor] Connected to Supabase.")
        except Exception as e:
            # Graceful degradation — print error and exit cleanly
            print(f"\n❌ Monitor init failed: {e}")
            print("Check your SUPABASE_URL and SUPABASE_KEY environment variables.\n")
            self.supabase = None

    def _divider(self, title: str):
        width = 80
        print("\n" + "=" * width)
        print(f"  {title}")
        print("=" * width)

    # =========================================================================
    # DISPLAY 1: System State
    # =========================================================================

    def display_system_state(self):
        """Shows current Fusion Score inputs for each pair."""
        self._divider("SYSTEM STATE — Fusion Score Inputs")

        if not self.supabase:
            print("  ⚠️  Not connected.")
            return

        try:
            response = self.supabase.table("system_state").select("*").execute()

            if not response.data:
                print("  No system state data found.")
                return

            table_data = []
            for row in response.data:
                cot_index = row.get('cot_index')
                idx_str   = f"{cot_index:.3f}" if cot_index is not None else "N/A"

                table_data.append([
                    row.get('pair',             'N/A'),
                    row.get('macro_sentiment',  0),
                    row.get('cot_bias',         'N/A'),
                    idx_str,
                    row.get('cot_net',          'N/A'),
                    row.get('cot_date',         'N/A'),
                    row.get('last_alerted_candle', 'N/A'),
                    str(row.get('last_updated', 'N/A'))[:19]
                ])

            print(tabulate(
                table_data,
                headers=[
                    'Pair', 'Sentiment', 'COT Bias', 'COT Index',
                    'COT Net', 'COT Date', 'Last Candle', 'Updated'
                ],
                tablefmt='grid'
            ))

        except Exception as e:
            print(f"  ❌ Error: {e}")

    # =========================================================================
    # DISPLAY 2: Recent Trades
    # =========================================================================

    def display_recent_trades(self, hours: int = 24):
        """Shows recent trades with pips and result."""
        self._divider(f"RECENT TRADES — Last {hours} Hours (max {DISPLAY_LIMIT})")

        if not self.supabase:
            print("  ⚠️  Not connected.")
            return

        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=hours)
            ).isoformat()

            response = (
                self.supabase
                .table("trade_logs")
                .select("*")
                .gte("timestamp_ist", cutoff)
                .order("timestamp_ist", desc=True)
                .limit(DISPLAY_LIMIT)
                .execute()
            )

            trades = response.data or []

            if not trades:
                print(f"  No trades in the last {hours} hours.")
                return

            table_data = []
            for t in trades:
                pips   = t.get('pips')
                result = t.get('result', 'PENDING')

                result_icon = {
                    'WIN':       '✅',
                    'LOSS':      '❌',
                    'BREAKEVEN': '➖',
                    'PENDING':   '⏳'
                }.get(result, '?')

                table_data.append([
                    str(t.get('timestamp_ist', 'N/A'))[:16],
                    t.get('pair',             'N/A'),
                    t.get('direction',        'N/A'),
                    t.get('confidence_score', 'N/A'),
                    t.get('cot_bias',         'N/A'),
                    t.get('sentiment',        'N/A'),
                    f"{t.get('entry_price', 0):.5f}",
                    f"{t.get('exit_price', 0):.5f}" if t.get('exit_price') else '—',
                    f"{pips:+.1f}" if pips is not None else '—',
                    f"{result_icon} {result}"
                ])

            print(tabulate(
                table_data,
                headers=[
                    'Time (IST)', 'Pair', 'Dir', 'Score', 'COT',
                    'Sent', 'Entry', 'Exit', 'Pips', 'Result'
                ],
                tablefmt='grid'
            ))

            # Stats
            total     = len(trades)
            wins      = sum(1 for t in trades if t.get('result') == 'WIN')
            losses    = sum(1 for t in trades if t.get('result') == 'LOSS')
            breakeven = sum(1 for t in trades if t.get('result') == 'BREAKEVEN')
            pending   = sum(1 for t in trades if t.get('result') not in ['WIN', 'LOSS', 'BREAKEVEN'])
            decided   = wins + losses + breakeven
            win_rate  = f"{wins / decided * 100:.1f}%" if decided > 0 else "N/A"

            total_pips = sum(
                t.get('pips', 0) or 0
                for t in trades
                if t.get('pips') is not None
            )

            print(f"\n  📊 Showing {total} trades | "
                  f"✅ {wins} W  ❌ {losses} L  ➖ {breakeven} BE  ⏳ {pending} Pending")
            print(f"  📈 Win Rate: {win_rate} | Net Pips: {total_pips:+.1f}")

        except Exception as e:
            print(f"  ❌ Error: {e}")

    # =========================================================================
    # DISPLAY 3: All-time Trade Stats
    # =========================================================================

    def display_trade_stats(self):
        """Shows all-time performance statistics."""
        self._divider("ALL-TIME TRADE STATISTICS")

        if not self.supabase:
            print("  ⚠️  Not connected.")
            return

        try:
            # Total count
            count_resp   = (
                self.supabase.table("trade_logs")
                .select("*", count="exact")
                .execute()
            )
            total_trades = count_resp.count or 0

            # Result breakdown (fetch all results only — small payload)
            all_resp  = (
                self.supabase.table("trade_logs")
                .select("pair, direction, confidence_score, result, pips")
                .execute()
            )
            all_data  = all_resp.data or []

            wins      = sum(1 for t in all_data if t.get('result') == 'WIN')
            losses    = sum(1 for t in all_data if t.get('result') == 'LOSS')
            breakeven = sum(1 for t in all_data if t.get('result') == 'BREAKEVEN')
            pending   = sum(1 for t in all_data if t.get('result') not in
                           ['WIN', 'LOSS', 'BREAKEVEN'])
            decided   = wins + losses + breakeven
            win_rate  = f"{wins / decided * 100:.1f}%" if decided > 0 else "N/A"
            net_pips  = sum(t.get('pips', 0) or 0 for t in all_data
                           if t.get('pips') is not None)

            print(f"\n  Total Signals : {total_trades}")
            print(f"  Wins          : {wins}")
            print(f"  Losses        : {losses}")
            print(f"  Breakeven     : {breakeven}")
            print(f"  Pending       : {pending}")
            print(f"  Win Rate      : {win_rate}")
            print(f"  Net Pips      : {net_pips:+.1f}")

            # Per-pair breakdown
            pairs = {}
            for t in all_data:
                p = t.get('pair', 'Unknown')
                if p not in pairs:
                    pairs[p] = {'W': 0, 'L': 0, 'BE': 0, 'pips': 0}
                r = t.get('result')
                if r == 'WIN':
                    pairs[p]['W'] += 1
                elif r == 'LOSS':
                    pairs[p]['L'] += 1
                elif r == 'BREAKEVEN':
                    pairs[p]['BE'] += 1
                pairs[p]['pips'] += t.get('pips', 0) or 0

            if pairs:
                print("\n  By Pair:")
                pair_table = []
                for pair, s in pairs.items():
                    d  = s['W'] + s['L'] + s['BE']
                    wr = f"{s['W']/d*100:.1f}%" if d > 0 else "N/A"
                    pair_table.append([
                        pair, s['W'], s['L'], s['BE'], wr, f"{s['pips']:+.1f}"
                    ])
                print(tabulate(
                    pair_table,
                    headers=['Pair', 'W', 'L', 'BE', 'Win%', 'Net Pips'],
                    tablefmt='simple'
                ))

            # Score tier breakdown
            score_tiers = {}
            for t in all_data:
                score = t.get('confidence_score')
                if score is None:
                    continue
                # Round to nearest 5 for tier grouping
                tier = (score // 5) * 5
                key  = f"Score {tier}-{tier+4}"
                if key not in score_tiers:
                    score_tiers[key] = {'W': 0, 'L': 0, 'BE': 0}
                r = t.get('result')
                if r == 'WIN':
                    score_tiers[key]['W'] += 1
                elif r == 'LOSS':
                    score_tiers[key]['L'] += 1
                elif r == 'BREAKEVEN':
                    score_tiers[key]['BE'] += 1

            if score_tiers:
                print("\n  By Score Tier:")
                tier_table = []
                for tier_name in sorted(score_tiers.keys()):
                    s = score_tiers[tier_name]
                    d = s['W'] + s['L'] + s['BE']
                    wr = f"{s['W']/d*100:.1f}%" if d > 0 else "N/A"
                    tier_table.append([tier_name, s['W'], s['L'], s['BE'], wr])
                print(tabulate(
                    tier_table,
                    headers=['Score Tier', 'W', 'L', 'BE', 'Win%'],
                    tablefmt='simple'
                ))

        except Exception as e:
            print(f"  ❌ Error: {e}")

    # =========================================================================
    # DISPLAY 4: Sentiment Summary
    # =========================================================================

    def display_sentiment_summary(self, hours: int = 24):
        """Shows recent sentiment analysis results."""
        self._divider(f"SENTIMENT ANALYSIS — Last {hours} Hours")

        if not self.supabase:
            print("  ⚠️  Not connected.")
            return

        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=hours)
            ).isoformat()

            response = (
                self.supabase
                .table("processed_sentiment")
                .select("*")
                .gte("created_at", cutoff)
                .order("created_at", desc=True)
                .limit(DISPLAY_LIMIT)
                .execute()
            )

            records = response.data or []

            if not records:
                print(f"  No sentiment data in the last {hours} hours.")
                return

            # Summary counts
            eur_bull = sum(1 for r in records if r.get('eur_usd_sentiment') == 'BULLISH')
            eur_bear = sum(1 for r in records if r.get('eur_usd_sentiment') == 'BEARISH')
            eur_neut = sum(1 for r in records if r.get('eur_usd_sentiment') == 'NEUTRAL')

            gbp_bull = sum(1 for r in records if r.get('gbp_usd_sentiment') == 'BULLISH')
            gbp_bear = sum(1 for r in records if r.get('gbp_usd_sentiment') == 'BEARISH')
            gbp_neut = sum(1 for r in records if r.get('gbp_usd_sentiment') == 'NEUTRAL')

            high_imp = sum(1 for r in records if r.get('importance_tier') == 'HIGH')
            med_imp  = sum(1 for r in records if r.get('importance_tier') == 'MEDIUM')
            low_imp  = sum(1 for r in records if r.get('importance_tier') == 'LOW')

            # Source breakdown (RSS and news only — Twitter removed)
            rss_count  = sum(1 for r in records if r.get('source') == 'rss')
            news_count = sum(1 for r in records if r.get('source') == 'news')

            # Model breakdown
            gemini_count = sum(1 for r in records if r.get('model_used') == 'Gemini')
            hf_count     = sum(1 for r in records if 'HuggingFace' in (r.get('model_used') or ''))

            print(f"\n  Showing {len(records)} records (last {hours}h)\n")

            print(f"  📈 EUR/USD:  🟢 {eur_bull} Bullish  🔴 {eur_bear} Bearish  ⚪ {eur_neut} Neutral")
            print(f"  📈 GBP/USD:  🟢 {gbp_bull} Bullish  🔴 {gbp_bear} Bearish  ⚪ {gbp_neut} Neutral")
            print(f"\n  ⭐ Importance: HIGH {high_imp}  MEDIUM {med_imp}  LOW {low_imp}")
            print(f"  📰 Sources:   RSS {rss_count}  News {news_count}")
            print(f"  🤖 Models:    Gemini {gemini_count}  FinBERT {hf_count}")

            # Recent signals table (top 10)
            print(f"\n  Recent Signals (top 10):")
            table_data = []
            for r in records[:10]:
                eur_conf = r.get('eur_usd_confidence', 0) or 0
                gbp_conf = r.get('gbp_usd_confidence', 0) or 0
                table_data.append([
                    str(r.get('created_at', ''))[:16],
                    r.get('source',           'N/A'),
                    r.get('importance_tier',  'N/A'),
                    r.get('eur_usd_sentiment','N/A'),
                    f"{eur_conf:.2f}",
                    r.get('gbp_usd_sentiment','N/A'),
                    f"{gbp_conf:.2f}",
                    r.get('model_used',       'N/A')[:12]
                ])

            print(tabulate(
                table_data,
                headers=[
                    'Time', 'Source', 'Tier',
                    'EUR Sent', 'EUR Conf',
                    'GBP Sent', 'GBP Conf',
                    'Model'
                ],
                tablefmt='simple'
            ))

        except Exception as e:
            print(f"  ❌ Error: {e}")

    # =========================================================================
    # DISPLAY 5: Raw Collection Stats
    # =========================================================================

    def display_raw_collection_stats(self):
        """Shows raw sentiment data collection totals."""
        self._divider("RAW SENTIMENT COLLECTION")

        if not self.supabase:
            print("  ⚠️  Not connected.")
            return

        try:
            total_resp = (
                self.supabase
                .table("raw_sentiment_data")
                .select("*", count="exact")
                .execute()
            )
            total = total_resp.count or 0

            # Source breakdown
            source_resp = (
                self.supabase
                .table("raw_sentiment_data")
                .select("source")
                .execute()
            )
            source_data = source_resp.data or []

            rss_count  = sum(1 for r in source_data if r.get('source') == 'rss')
            news_count = sum(1 for r in source_data if r.get('source') == 'news')

            print(f"\n  Total Raw Items Collected : {total}")
            print(f"  RSS (ForexLive)           : {rss_count}")
            print(f"  News (GNews)              : {news_count}")

        except Exception as e:
            print(f"  ❌ Error: {e}")

    # =========================================================================
    # FULL DASHBOARD
    # =========================================================================

    def run_full_monitor(self):
        """Runs the complete monitoring dashboard."""
        print("\n")
        self._divider(
            f"FUSION SCORE BOT — SUPABASE MONITOR  "
            f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}]"
        )

        if not self.supabase:
            print("\n❌ Cannot run monitor — no database connection.\n")
            return

        self.display_system_state()
        self.display_recent_trades(hours=24)
        self.display_trade_stats()
        self.display_sentiment_summary(hours=24)
        self.display_raw_collection_stats()

        self._divider("END OF REPORT")
        print()


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    monitor = SupabaseMonitor()
    monitor.run_full_monitor()
        
