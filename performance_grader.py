import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import pytz
import logging
from shared_functions import get_supabase_client, send_error_notification

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Map your standard pairs to Yahoo Finance tickers
YF_TICKERS = {
    'EUR/USD': 'EURUSD=X',
    'GBP/USD': 'GBPUSD=X'
}

def grade_trades():
    """
    Grades trades in Supabase by downloading market data from Yahoo Finance.
    Trades are held for exactly 1 hour.
    """
    try:
        logger.info("Connecting to Supabase...")
        supabase = get_supabase_client()
        
        # Fetch trades that have an entry_price but no result
        response = supabase.table("trade_logs").select("*").is_("result", "null").not_.is_("entry_price", "null").execute()
        ungraded_trades = response.data if response.data else []
        
        if not ungraded_trades:
            logger.info("All trades are already graded. Standing by.")
            return

        logger.info(f"Found {len(ungraded_trades)} ungraded trades. Downloading market data...")
        
        # Download recent market data (last 5 days)
        market_data = {}
        for pair, ticker in YF_TICKERS.items():
            df = yf.download(ticker, period="5d", interval="15min", progress=False)
            if not df.empty:
                # Standardize to IST to match trade logs
                if df.index.tz is None:
                    df.index = df.index.tz_localize('UTC')
                df.index = df.index.tz_convert('Asia/Kolkata')
                market_data[pair] = df

        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)

        for trade in ungraded_trades:
            trade_id = trade['id']
            pair = trade['pair']
            entry_time_str = trade['timestamp_ist']
            direction = trade['direction']
            entry_price = float(trade['entry_price'])
            
            try:
                # Supabase timestamp is usually ISO format
                entry_time = datetime.fromisoformat(entry_time_str.replace('Z', '+00:00'))
                if entry_time.tzinfo is None:
                    entry_time = pytz.utc.localize(entry_time)
                entry_time = entry_time.astimezone(ist)
            except Exception as e:
                logger.warning(f"Skipping trade {trade_id} due to time parsing error: {e}")
                continue
                
            # Strategy holds for exactly 1 hour
            exit_time = entry_time + timedelta(hours=1)
            
            if now_ist < exit_time:
                logger.info(f"Trade {trade_id} ({pair}) is still open. Skipping.")
                continue
                
            df = market_data.get(pair)
            if df is None or df.empty:
                logger.warning(f"No market data for {pair}. Skipping.")
                continue
                
            # Find the first candle at or after the 1-hour exit time
            future_data = df[df.index >= exit_time]
            if future_data.empty:
                logger.info(f"Market data not yet available for exit time {exit_time} of trade {trade_id}. Skipping.")
                continue
                
            exit_price = float(future_data.iloc[0]['Close'])
            
            # Calculate Pips (Standard Forex scaling factor is 10,000)
            # Note: For JPY pairs, this would be 100, but we only have EUR/USD and GBP/USD.
            multiplier = 10000
            if direction == "LONG":
                pips = (exit_price - entry_price) * multiplier
            else:
                pips = (entry_price - exit_price) * multiplier
                
            result = "WIN" if pips > 0 else "LOSS"
            
            logger.info(f"Grading Trade {trade_id}: {pair} {direction} | Entry: {entry_price:.5f} | Exit: {exit_price:.5f} | Pips: {pips:.1f} | {result}")
            
            # Update Supabase
            supabase.table("trade_logs").update({
                "exit_price": exit_price,
                "pips": round(pips, 1),
                "result": result
            }).eq("id", trade_id).execute()

        logger.info("Grading complete.")
            
    except Exception as e:
        error_msg = f"Performance Grader Failed: {str(e)}"
        logger.error(error_msg)
        send_error_notification(error_msg)

if __name__ == "__main__":
    grade_trades()
