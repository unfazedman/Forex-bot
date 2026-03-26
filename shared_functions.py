import os
import json
import gspread
from config import WEIGHT_ATR, WEIGHT_SENTIMENT, WEIGHT_COT

def get_gspread_client():
    """
    Returns an authenticated Google Sheets client.
    Automatically detects if it is running on GitHub (file) or Render (env var).
    """
    # 1. Check for physical file (Used by GitHub Actions)
    if os.path.exists('credentials.json'):
        return gspread.service_account(filename='credentials.json')
    
    # 2. Check for environment variable (Used by live Render server)
    creds_json = os.environ.get('GCP_CREDENTIALS')
    if creds_json:
        creds_dict = json.loads(creds_json)
        return gspread.service_account_from_dict(creds_dict)
        
    raise Exception("CRITICAL: Google Cloud Credentials not found in environment or file.")

def calculate_fusion_score(sentiment, atr_multiplier, cot_bias, pair_direction):
    """The Master Algorithm for calculating trade viability."""
    score = 50 
    
    # 1. Volatility Expansion
    if atr_multiplier >= 1.5: 
        score += WEIGHT_ATR
        
    # 2. AI Macro Sentiment
    if pair_direction == "LONG":
        if sentiment <= -5: score += WEIGHT_SENTIMENT 
        elif sentiment >= 5: score -= WEIGHT_SENTIMENT 
    else: 
        if sentiment >= 5: score += WEIGHT_SENTIMENT   
        elif sentiment <= -5: score -= WEIGHT_SENTIMENT
        
    # 3. Institutional COT Bias
    if cot_bias == "BULLISH" and pair_direction == "LONG": 
        score += WEIGHT_COT
    elif cot_bias == "BEARISH" and pair_direction == "SHORT": 
        score += WEIGHT_COT
    elif cot_bias != "NEUTRAL": 
        score -= WEIGHT_COT 
        
    return max(0, min(100, score))
      
