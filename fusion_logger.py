import gspread
import pytz
from datetime import datetime

def calculate_fusion_score(sentiment, atr_multiplier, cot_bias):
    # This is the Phase 1 Fusion Algorithm (0-100 Scale)
    score = 50 # Baseline neutral
    
    # 1. Volatility Weight (Is institutional volume present?)
    if atr_multiplier >= 1.5:
        score += 20
        
    # 2. Sentiment Weight (Is the daily macro news aligning?)
    if sentiment >= 5: # Bullish USD / Bearish EUR
        score -= 15
    elif sentiment <= -5: # Bearish USD / Bullish EUR
        score += 15
        
    # 3. Smart Money Weight (Are we trading with the hedge funds?)
    if cot_bias == "BULLISH":
        score += 15
        
    # Cap the score between 0 and 100
    return max(0, min(100, score))

def log_signal():
    print("Connecting to Google Cloud Laboratory...\n")
    
    try:
        # 1. Unlock the database using your JSON key
        gc = gspread.service_account(filename='credentials.json')
        sheet = gc.open("Quant Performance Log").sheet1
    except Exception as e:
        print(f"Database Connection Failed: {e}")
        return

    # 2. Generate a precise IST Timestamp
    ist = pytz.timezone('Asia/Kolkata')
    timestamp = datetime.now(ist).strftime('%Y-%m-%d %I:%M:%S %p')

    # 3. The Mock Data (Simulating a live market event)
    pair = "EUR/USD"
    v1_sentiment = -8        # Macro is Bearish USD (Bullish for EUR)
    v2_atr = 1.6             # Massive 1.6x Volatility Expansion
    v3_cot = "BULLISH"       # Hedge Funds are Net Long EUR
    entry_price = 1.08500

    # 4. Calculate the Fusion Score
    fusion_score = calculate_fusion_score(v1_sentiment, v2_atr, v3_cot)

    # 5. Format the row perfectly to match your spreadsheet columns
    row_data = [
        timestamp, 
        pair, 
        v1_sentiment, 
        v2_atr, 
        v3_cot, 
        f"{fusion_score}/100", 
        entry_price
    ]

    # 6. Push to the cloud
    try:
        sheet.append_row(row_data)
        print(f"✅ SUCCESS: High-Probability Signal Logged!")
        print(f"📊 {pair} | Fusion Score: {fusion_score}/100 | Action: LONG")
        print("\n--> Open your Google Sheets app to verify.")
    except Exception as e:
        print(f"Failed to write row: {e}")

if __name__ == "__main__":
    log_signal()
  
