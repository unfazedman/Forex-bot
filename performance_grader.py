import os
import json
import gspread
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import pytz

def grade_performance():
    print("Connecting to Central Brain...")
    creds_dict = json.loads(os.environ.get('GCP_CREDENTIALS'))
    gc = gspread.service_account_from_dict(creds_dict)
    sheet = gc.open("Quant Performance Log").sheet1
    
    rows = sheet.get_all_values()
    
    print("Downloading massive historical dataset (Yahoo Finance)...")
    # Using YF to bypass API rate limits for massive batch processing
    eur = yf.download('EURUSD=X', interval='15m', period='60d')
    gbp = yf.download('GBPUSD=X', interval='15m', period='60d')
    
    ist = pytz.timezone('Asia/Kolkata')
    eur.index = eur.index.tz_convert(ist)
    gbp.index = gbp.index.tz_convert(ist)
    
    updates = []
    graded_count = 0
    
    for i, row in enumerate(rows[1:], start=2): # Skip the header
        # Expand row length dynamically if it's missing columns
        while len(row) < 11: row.append("")
            
        # If we already graded this trade (Exit Price exists), skip it
        if row[8] != "": 
            continue
            
        timestamp_str = row[0]
        pair = row[1]
        
        try:
            entry_price = float(row[6])
            
            # Convert string to exact IST datetime object
            dt = datetime.strptime(timestamp_str, '%Y-%m-%d %I:%M:%S %p')
            dt = ist.localize(dt)
            
            df = eur if pair == "EUR/USD" else gbp
            
            # 1. Determine Direction from Entry Candle
            idx_entry = df.index.get_indexer([dt], method='nearest')[0]
            open_price = float(df['Open'].values[idx_entry])
            close_price = float(df['Close'].values[idx_entry])
            direction = "LONG" if close_price > open_price else "SHORT"
            
            # 2. Find the Exit Candle (Exactly 1 Hour Later)
            exit_time = dt + timedelta(hours=1)
            idx_exit = df.index.get_indexer([exit_time], method='nearest')[0]
            exit_price = float(df['Close'].values[idx_exit])
            
            # 3. Calculate exact Pip movement
            if direction == "LONG":
                pips = (exit_price - entry_price) * 10000
            else:
                pips = (entry_price - exit_price) * 10000
                
            result = "WIN 🟢" if pips > 0 else "LOSS 🔴"
            
            # 4. Prepare batch update
            updates.append({'range': f'H{i}', 'values': [[direction]]})
            updates.append({'range': f'I{i}', 'values': [[round(exit_price, 5)]]})
            updates.append({'range': f'J{i}', 'values': [[round(pips, 1)]]})
            updates.append({'range': f'K{i}', 'values': [[result]]})
            
            graded_count += 1
            print(f"Graded {pair} at {timestamp_str}: {result} ({round(pips,1)} pips)")
            
        except Exception as e:
            print(f"Skipping row {i} due to error: {e}")
            
    if updates:
        print(f"\nPushing {graded_count} graded trades to Google Sheets...")
        sheet.batch_update(updates) # Pushes everything at once to prevent Google API bans
        print("✅ Forward Test Grading Complete!")
    else:
        print("All trades are already graded. Waiting for new signals.")

if __name__ == "__main__":
    grade_performance()
  
