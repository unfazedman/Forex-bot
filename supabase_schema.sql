-- =============================================================================
-- Fusion Score Bot V6.0 — Supabase Schema
-- =============================================================================
-- Run this entire file in Supabase SQL Editor to initialize or upgrade.
-- All statements use IF NOT EXISTS / IF NOT EXISTS equivalents so it is
-- safe to run multiple times without data loss.
--
-- Changes from V5 schema:
--   system_state    : + cot_index, cot_net, cot_date, last_alerted_candle
--   trade_logs      : confidence_score changed VARCHAR → INT
--                     volatility_multiplier changed VARCHAR → FLOAT
--   Indexes added   : trade_logs(pair), trade_logs(timestamp_ist),
--                     trade_logs(result), processed_sentiment(created_at),
--                     processed_sentiment(importance_tier)
-- =============================================================================


-- =============================================================================
-- TABLE 1: system_state
-- Single row per pair. Holds the latest signal state for each pair.
-- Written by: cot_tracker.py, sentiment_scanner.py, volatility_atr.py
-- Read by:    volatility_atr.py (Fusion Score input)
-- =============================================================================

CREATE TABLE IF NOT EXISTS system_state (
    id                   SERIAL PRIMARY KEY,
    pair                 VARCHAR(10)  NOT NULL UNIQUE,  -- 'EUR/USD' or 'GBP/USD'

    -- Sentiment (written by sentiment_scanner via aggregate_and_push_sentiment)
    -- Range: -10 to +10. Positive = Bullish for pair. 0 = neutral/no data.
    macro_sentiment      INT          DEFAULT 0,

    -- COT (written by cot_tracker.py — 5-state system)
    cot_bias             VARCHAR(20)  DEFAULT 'NEUTRAL',
    -- STRONGLY_BULLISH | BULLISH | NEUTRAL | BEARISH | STRONGLY_BEARISH

    cot_index            FLOAT,       -- 52-week normalized index (0.0 to 1.0)
    cot_net              INT,         -- Raw non-commercial net position
    cot_date             VARCHAR(10), -- Date of the CFTC report (YYYY-MM-DD)

    -- Deduplication (written by volatility_atr.py to survive Render restarts)
    last_alerted_candle  VARCHAR(30), -- datetime string of last signal candle

    last_updated         TIMESTAMPTZ  DEFAULT NOW()
);

-- Seed default rows for both pairs (safe to run again — ON CONFLICT does nothing)
INSERT INTO system_state (pair, macro_sentiment, cot_bias)
VALUES
    ('EUR/USD', 0, 'NEUTRAL'),
    ('GBP/USD', 0, 'NEUTRAL')
ON CONFLICT (pair) DO NOTHING;


-- =============================================================================
-- TABLE 2: trade_logs
-- One row per trade signal fired by volatility_atr.py.
-- Graded nightly by performance_grader.py.
-- =============================================================================

CREATE TABLE IF NOT EXISTS trade_logs (
    id                   SERIAL PRIMARY KEY,
    timestamp_ist        TIMESTAMPTZ  DEFAULT NOW(),
    pair                 VARCHAR(10)  NOT NULL,
    direction            VARCHAR(5),              -- 'LONG' or 'SHORT'

    -- Fusion Score inputs at time of signal
    sentiment            INT,                     -- macro_sentiment value used
    volatility_multiplier FLOAT,                  -- ATR multiplier (e.g. 1.8)
    cot_bias             VARCHAR(20),             -- COT state used

    -- Signal output
    -- V5 bug: this was VARCHAR(20). Fixed to INT for numeric queries.
    confidence_score     INT,                     -- Fusion Score 0-100

    -- Trade lifecycle
    entry_price          FLOAT,
    exit_price           FLOAT,
    pips                 FLOAT,

    -- V5 had only WIN/LOSS. Added BREAKEVEN for 0-pip trades.
    result               VARCHAR(10),             -- 'WIN' | 'LOSS' | 'BREAKEVEN'

    flag                 VARCHAR(50)              -- optional manual annotation
);

-- Indexes for common query patterns
-- Without these, every pair/time/result filter does a full table scan.
CREATE INDEX IF NOT EXISTS idx_trade_logs_pair
    ON trade_logs (pair);

CREATE INDEX IF NOT EXISTS idx_trade_logs_timestamp
    ON trade_logs (timestamp_ist DESC);

CREATE INDEX IF NOT EXISTS idx_trade_logs_result
    ON trade_logs (result);

CREATE INDEX IF NOT EXISTS idx_trade_logs_pair_result
    ON trade_logs (pair, result);


-- =============================================================================
-- TABLE 3: raw_sentiment_data
-- Stores every collected article BEFORE filtering.
-- Used for debugging, backtesting, future ML training.
-- Written by: sentiment_scanner.py (Layer 3)
-- =============================================================================

CREATE TABLE IF NOT EXISTS raw_sentiment_data (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    text         TEXT         NOT NULL,
    source       VARCHAR(50),             -- 'rss' | 'news'
    timestamp    TIMESTAMPTZ,             -- publication time
    author       VARCHAR(255),
    url          TEXT,
    raw_metadata JSONB,
    created_at   TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_sentiment_created
    ON raw_sentiment_data (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_raw_sentiment_source
    ON raw_sentiment_data (source);


-- =============================================================================
-- TABLE 4: processed_sentiment
-- Stores sentiment analysis output after full pipeline processing.
-- Written by: sentiment_scanner.py (Layer 10)
-- Read by:    sentiment_scanner.py aggregate_and_push_sentiment() (Layer 11)
--             supabase_monitor.py
--             system_health_check.py
-- =============================================================================

CREATE TABLE IF NOT EXISTS processed_sentiment (
    id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    text_cleaned         TEXT,
    source               VARCHAR(50),
    timestamp            TIMESTAMPTZ,             -- original article publish time
    importance_score     FLOAT,
    importance_tier      VARCHAR(10),             -- 'HIGH' | 'MEDIUM' | 'LOW'
    eur_usd_sentiment    VARCHAR(10),             -- 'BULLISH' | 'BEARISH' | 'NEUTRAL'
    eur_usd_confidence   FLOAT,
    gbp_usd_sentiment    VARCHAR(10),
    gbp_usd_confidence   FLOAT,
    model_used           VARCHAR(30),             -- 'Gemini' | 'HuggingFace-FinBERT'
    processing_time_ms   INT,
    created_at           TIMESTAMPTZ  DEFAULT NOW()
);

-- created_at is the primary filter in all sentiment queries
CREATE INDEX IF NOT EXISTS idx_processed_sentiment_created
    ON processed_sentiment (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_processed_sentiment_tier
    ON processed_sentiment (importance_tier);

CREATE INDEX IF NOT EXISTS idx_processed_sentiment_eur
    ON processed_sentiment (eur_usd_sentiment);

CREATE INDEX IF NOT EXISTS idx_processed_sentiment_gbp
    ON processed_sentiment (gbp_usd_sentiment);


-- =============================================================================
-- MIGRATION: Upgrade existing V5 tables to V6 schema
-- Run these ALTER statements if upgrading an existing database.
-- Safe to skip if running fresh (CREATE TABLE above handles it).
-- Comment out any columns that already exist in your database.
-- =============================================================================

-- system_state additions
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS cot_index           FLOAT;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS cot_net             INT;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS cot_date            VARCHAR(10);
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS last_alerted_candle VARCHAR(30);

-- trade_logs type fixes
-- NOTE: These will fail if rows already exist with non-numeric data in
-- confidence_score or volatility_multiplier. In that case:
--   1. Export your data first
--   2. Drop and recreate the columns
--   3. Re-import cleaned data
ALTER TABLE trade_logs ALTER COLUMN confidence_score
    TYPE INT USING confidence_score::INT;

ALTER TABLE trade_logs ALTER COLUMN volatility_multiplier
    TYPE FLOAT USING REPLACE(volatility_multiplier::TEXT, 'x', '')::FLOAT;

-- Add BREAKEVEN to result (no schema change needed — VARCHAR(10) already fits)
-- Just a documentation note: result now accepts 'WIN', 'LOSS', 'BREAKEVEN'
