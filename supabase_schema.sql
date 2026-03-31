-- 1. System State Table (Replacement for GSheets 'System State' tab)
CREATE TABLE IF NOT EXISTS system_state (
    id SERIAL PRIMARY KEY,
    pair VARCHAR(10) NOT NULL UNIQUE, -- 'EUR/USD', 'GBP/USD'
    macro_sentiment INT DEFAULT 0,
    cot_bias VARCHAR(20) DEFAULT 'NEUTRAL',
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

-- Initialize with default values
INSERT INTO system_state (pair, macro_sentiment, cot_bias) 
VALUES ('EUR/USD', 0, 'NEUTRAL'), ('GBP/USD', 0, 'NEUTRAL')
ON CONFLICT (pair) DO NOTHING;

-- 2. Trade Logs Table (Replacement for GSheets 'Sheet1' tab)
CREATE TABLE IF NOT EXISTS trade_logs (
    id SERIAL PRIMARY KEY,
    timestamp_ist TIMESTAMPTZ DEFAULT NOW(),
    pair VARCHAR(10) NOT NULL,
    sentiment INT,
    volatility_multiplier VARCHAR(20),
    cot_bias VARCHAR(20),
    confidence_score VARCHAR(20),
    entry_price FLOAT,
    direction VARCHAR(10),
    exit_price FLOAT,
    pips FLOAT,
    result VARCHAR(10),
    flag VARCHAR(50)
);

-- 3. Raw Sentiment Data (Advanced Scanner Layer 3)
CREATE TABLE IF NOT EXISTS raw_sentiment_data (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text TEXT NOT NULL,
    source VARCHAR(50),
    timestamp TIMESTAMPTZ,
    author VARCHAR(255),
    engagement JSONB,
    url TEXT,
    raw_metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Processed Sentiment (Advanced Scanner Layer 10)
CREATE TABLE IF NOT EXISTS processed_sentiment (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text_cleaned TEXT,
    source VARCHAR(50),
    timestamp TIMESTAMPTZ,
    importance_score FLOAT,
    importance_tier VARCHAR(20),
    eur_usd_sentiment VARCHAR(20),
    eur_usd_confidence FLOAT,
    gbp_usd_sentiment VARCHAR(20),
    gbp_usd_confidence FLOAT,
    model_used VARCHAR(50),
    processing_time_ms INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
