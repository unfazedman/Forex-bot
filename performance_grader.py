import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import pytz
import logging

# --- PLUG INTO THE CENTRAL NERVOUS SYSTEM ---
from config import SHEET_NAME, LOG_TAB
from shared_functions import get_gspread_client, send_error_notification

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Map your standard pairs to Yahoo Finance tickers
YF_TICKERS = {
    'EUR/USD': 'EURUSD=X',
    'GBP/USD': 'GBPUSD=X'
}

def grade_trades():
    try:
        logging.info("Connecting to Central Brain...")
        gc = get_gspread_client()
        sheet = gc.open(SHEET_NAME).worksheet(LOG_TAB)
        records = sheet.get_all_records()
        
        if not records:
            logging.info("No trades logged yet. Standing by.")
            return

        # Find trades that have an Entry Price but no Exit Price
        ungraded_rows = []
        for i, row in enumerate(records):
            # i + 2 because row 1 is the header, and Python is 0-indexed
            if not row.get('Exit Price') and row.get('Entry Price'):
                ungraded_rows.append((i + 2, row))

        if not ungraded_rows:
            logging.info("All trades are already graded. Standing by.")
            return

        logging.info(f"Found {len(ungraded_rows)} ungraded trades. Downloading optimized 5-day market data...")
        
        # PHASE 4 FIX: Download only 5 days of data instead of 60 days to prevent RAM bloat
        market_data = {}
        for pair, ticker in YF_TICKERS.items():
            df = yf.download(ticker, period="5d", interval="15m", progress=False)
            if not df.empty:
                # Standardize Yahoo Finance timestamps to IST to match the Google Sheet
                if df.index.tz is None:
                    df.index = df.index.tz_localize('UTC')
                df.index = df.index.tz_convert('Asia/Kolkata')
                market_data[pair] = df

        updates = []
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)

        for row_idx, row in ungraded_rows:
            pair = row.get('Pair')
            # Handle slight variations in Google Sheet column names
            entry_time_str = row.get('Timestamp (IST)') or row.get('Timestamp') 
            direction = row.get('Direction')
            entry_price = float(row.get('Entry Price', 0))
            
            try:
                entry_time = datetime.strptime(entry_time_str, '%Y-%m-%d %I:%M:%S %p')
                entry_time = ist.localize(entry_time)
            except Exception as e:
                logging.warning(f"Skipping row {row_idx} due to time parsing error: {e}")
                continue
                
            # The strategy holds the trade for exactly 1 hour
            exit_time = entry_time + timedelta(hours=1)
            
            if now_ist < exit_time:
                logging.info(f"Trade at row {row_idx} is still open (1 hour hasn't passed). Skipping.")
                continue
                
            df = market_data.get(pair)
            if df is None or df.empty:
                continue
                
            # Find the first candle that printed at or directly after the 1-hour exit time
            future_data = df[df.index >= exit_time]
            if future_data.empty:
                logging.info(f"Market data not yet available for exit time {exit_time}. Skipping.")
                continue
                
            exit_price = float(future_data.iloc[0]['Close'])
            
            # Calculate Pips (Standard Forex scaling factor is 10,000)
            if direction == "LONG":
                pips = (exit_price - entry_price) * 10000
            else:
                pips = (entry_price - exit_price) * 10000
                
            result = "WIN" if pips > 0 else "LOSS"
            
            logging.info(f"Graded Row {row_idx}: {pair} {direction} | Entry: {entry_price:.5f} | Exit: {exit_price:.5f} | Pips: {pips:.1f} | {result}")
            
            # Columns: I=Exit Price, J=Pips, K=Result (Assuming standard layout: A-Timestamp, B-Pair, C-Sent, D-Mult, E-COT, F-Score, G-Entry, H-Dir)
            updates.append({'range': f'I{row_idx}', 'values': [[exit_price]]})
            updates.append({'range': f'J{row_idx}', 'values': [[round(pips, 1)]]})
            updates.append({'range': f'K{row_idx}', 'values': [[result]]})

        if updates:
            logging.info("Pushing grades to Central Brain (Google Sheets)...")
            sheet.batch_update(updates)
            logging.info("Grading complete.")
            
    except Exception as e:
        error_msg = f"Performance Grader Failed: {str(e)}"
        logging.error(error_msg)
        send_error_notification(error_msg) # Global Killswitch Alert

if __name__ == "__main__":
    grade_trades()
