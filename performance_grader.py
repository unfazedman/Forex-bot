import os
import gspread
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import pytz

def grade_performance():
    print("Connecting to Central Brain...")
    
    # THE FIX: Read from the physical file we generated in the YAML
    try:
        gc = gspread.service_account(filename='credentials.json')
        sheet = gc.open("Quant Performance Log").sheet1
    except Exception as e:
        print(f"Failed to connect to Google Sheets: {e}")
        return
        
    rows = sheet.get_all_values()
    
    print("Downloading massive historical dataset (Yahoo Finance)...")
    eur = yf.download('EURUSD=X', interval='15m', period='60d')
    gbp = yf.download('GBPUSD=X', interval='15m', period='60d')
    
    ist = pytz.timezone('Asia/Kolkata')
    eur.index = eur.index.tz_convert(ist)
    gbp.index = gbp.index.tz_convert(ist)
    
    updates = []
    graded_count = 0
    
    for i, row in enumerate(rows[1:], start=2): 
        while len(row) < 11: row.append("")
            
        if row[8] != "": 
            continue
            
        timestamp_str = row[0]
        pair = row[1]
        
        try:
            entry_price = float(row[6])
            dt = datetime.strptime(timestamp_str, '%Y-%m-%d %I:%M:%S %p')
            dt = ist.localize(dt)
            
            df = eur if pair == "EUR/USD" else gbp
            
            idx_entry = df.index.get_indexer([dt], method='nearest')[0]
            open_price = float(df['Open'].values[idx_entry])
            close_price = float(df['Close'].values[idx_entry])
            direction = "LONG" if close_price > open_price else "SHORT"
            
            exit_time = dt + timedelta(hours=1)
            idx_exit = df.index.get_indexer([exit_time], method='nearest')[0]
            exit_price = float(df['Close'].values[idx_exit])
            
            if direction == "LONG":
                pips = (exit_price - entry_price) * 10000
            else:
                pips = (entry_price - exit_price) * 10000
                
            result = "WIN 🟢" if pips > 0 else "LOSS 🔴"
            
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
        sheet.batch_update(updates) 
        print("✅ Forward Test Grading Complete!")
    else:
        print("All trades are already graded. Waiting for new signals.")

if __name__ == "__main__":
    grade_performance()
        
