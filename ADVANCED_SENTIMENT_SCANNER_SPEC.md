# Advanced Sentiment Scanner Pipeline Specification

## Core Principles (LOCKED)

The sentiment scanner pipeline must adhere to four immutable principles:

1. **Reliable**: No breaks, graceful degradation, multi-layer fallbacks
2. **Clean**: No garbage data, strict preprocessing, deduplication
3. **Prioritized**: Important signals prioritized over noise, time-decay applied
4. **AI-Ready**: Fast processing, structured output, minimal latency

## Full Pipeline Architecture

```
[Data Sources: GNews + Twitter]
         ↓
[Collector Layer: Fetch & Standardize]
         ↓
[Raw Storage: Store Everything]
         ↓
[Cleaning Engine: Remove Noise]
         ↓
[Deduplication Engine: Remove Duplicates]
         ↓
[Relevance Filter: Keep Only Important]
         ↓
[Importance Scoring: Assign Priority]
         ↓
[AI Router: Route to HF or Gemini]
         ↓
[Sentiment Engine: Generate Output]
         ↓
[Final Storage: Supabase]
         ↓
[Dashboard & Alerts]
```

## Layer 1: Data Sources (INPUT)

**Sources (V1)**:
- **GNews API**: Financial headlines (inflation, rates, GDP, forex)
- **Twitter/X (snscrape)**: Real-time sentiment from verified traders and institutions

**Rationale**: Two sources provide breadth (news) and velocity (social), without overwhelming the pipeline.

## Layer 2: Collector Engine

**Frequency**: Every 5–10 minutes

**Tasks**:
1. Fetch latest data from both sources
2. Convert to standardized format
3. Assign unique ID and timestamp

**Standard Data Format**:
```json
{
  "id": "uuid-v4",
  "text": "Federal Reserve signals rate hike in Q2",
  "source": "news",
  "timestamp": "2026-03-31T10:30:00Z",
  "author": "Reuters",
  "engagement": {
    "likes": 1200,
    "retweets": 450,
    "views": 50000
  },
  "url": "https://...",
  "raw_metadata": {}
}
```

## Layer 3: Raw Storage (CRITICAL)

**Purpose**: Store ALL collected data before filtering.

**Why**:
- Debugging and error analysis
- Backtesting and model validation
- Future machine learning training

**Storage Method**: Supabase table `raw_sentiment_data`

**Schema**:
```sql
CREATE TABLE raw_sentiment_data (
  id UUID PRIMARY KEY,
  text TEXT NOT NULL,
  source VARCHAR(50),
  timestamp TIMESTAMPTZ,
  author VARCHAR(255),
  engagement JSONB,
  url TEXT,
  raw_metadata JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## Layer 4: Cleaning Engine

**Tasks**:
1. Remove URLs
2. Remove emojis and special characters
3. Remove spam patterns (repeated characters, gibberish)
4. Normalize whitespace
5. Convert to lowercase
6. Trim to 300–500 characters

**Output Format**:
```json
{
  "id": "uuid-v4",
  "text_cleaned": "federal reserve signals rate hike in q2",
  "text_original": "Federal Reserve signals rate hike in Q2",
  "source": "news",
  "timestamp": "2026-03-31T10:30:00Z"
}
```

## Layer 5: Deduplication Engine

**Logic**: Remove near-duplicate content using text similarity.

**Threshold**: If similarity > 85%, keep only the highest-engagement version.

**Algorithm**: Cosine similarity on TF-IDF vectors or fuzzy string matching.

**Example**:
```
Input 1: "Fed raises rates by 0.25%"
Input 2: "Federal Reserve hikes interest rate 0.25%"
Similarity: 92% → Keep Input 2 (higher engagement)
```

**Output**: Deduplicated dataset

## Layer 6: Relevance Filter

**Purpose**: Remove useless data BEFORE AI processing (saves costs and time).

**Keep ONLY if**:

**News Articles**:
- Contains financial keywords: inflation, interest rate, GDP, oil, forex, employment, CPI, NFP, ECB, BOJ, RBI, Fed, Powell, Lagarde, etc.
- Source is reputable (Reuters, Bloomberg, AP, etc.)

**Twitter Posts**:
- Engagement > 1,000 likes OR verified user OR contains financial keyword

**Output**: Filtered dataset (typically 30–50% of input)

## Layer 7: Importance Scoring (EDGE)

**Purpose**: Assign priority scores to determine routing and processing order.

**Scoring System**:

| Factor | Score | Notes |
|--------|-------|-------|
| Central Bank (Fed, ECB, BOJ, RBI) | +3 | Highest impact |
| Inflation / GDP / Employment | +2 | High impact |
| Market Movement / Earnings | +1 | Medium impact |
| Engagement Boost (Twitter) | +0.5 per 1K likes | Scales with virality |
| Time Decay | -0.5 per hour | Older = less important |

**Final Score Tiers**:
- **HIGH** (≥ 4): Central bank news, major economic data
- **MEDIUM** (2–3): Inflation reports, earnings, market moves
- **LOW** (≤ 1): Minor news, low engagement

**Output**:
```json
{
  "id": "uuid-v4",
  "text": "federal reserve signals rate hike in q2",
  "importance_score": 4.5,
  "importance_tier": "HIGH",
  "factors": {
    "source_weight": 3,
    "keyword_weight": 1,
    "engagement_boost": 0.5,
    "time_decay": 0
  }
}
```

## Layer 8: AI Router (BRAIN)

**Decision Logic**:

```
IF importance_tier == "HIGH"
  → Send to Gemini (high-cost, high-accuracy model)
ELSE IF text_length < 100 AND simple_keywords_only
  → Send to HuggingFace (fast, low-cost)
ELSE
  → Send to HuggingFace
  → IF confidence < 0.7
    → Re-route to Gemini for validation
```

**Rationale**: Optimize cost and latency by routing simple cases to HF and complex cases to Gemini.

## Layer 9: Sentiment Engine

**Gemini Prompt** (for HIGH importance):
```
Analyze this financial news for EUR/USD and GBP/USD impact.
Output JSON:
{
  "eur_usd": {
    "sentiment": "Bullish|Bearish|Neutral",
    "confidence": 0.0-1.0,
    "reasoning": "..."
  },
  "gbp_usd": {
    "sentiment": "Bullish|Bearish|Neutral",
    "confidence": 0.0-1.0,
    "reasoning": "..."
  }
}

News: "{text}"
```

**HuggingFace Model**: Use `distilbert-base-uncased-finetuned-sst-2-english` or similar for quick classification.

**Output Format**:
```json
{
  "id": "uuid-v4",
  "text": "federal reserve signals rate hike in q2",
  "sentiment_output": {
    "eur_usd": {
      "sentiment": "Bearish",
      "confidence": 0.88,
      "reasoning": "Rate hike strengthens USD"
    },
    "gbp_usd": {
      "sentiment": "Neutral",
      "confidence": 0.65,
      "reasoning": "BOE policy divergence unclear"
    }
  },
  "model_used": "Gemini",
  "processing_time_ms": 1250
}
```

## Layer 10: Final Storage

**Destination**: Supabase table `processed_sentiment`

**Schema**:
```sql
CREATE TABLE processed_sentiment (
  id UUID PRIMARY KEY,
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
```

## Layer 11: Output (Dashboard & Alerts)

**Display Format**:
```
[HIGH] Federal Reserve signals rate hike in Q2
├─ EUR/USD: Bearish (0.88)
├─ GBP/USD: Neutral (0.65)
├─ Source: News (Reuters)
├─ Model: Gemini
└─ Time: 2026-03-31 10:30 IST
```

## Fail-Safe System (NON-NEGOTIABLE)

**Multi-Layer Fallback**:

1. **Gemini Timeout**: Fall back to HuggingFace
2. **HuggingFace Failure**: Use last-known sentiment for that pair
3. **API Rate Limit**: Queue and retry in next cycle
4. **Data Corruption**: Log error and skip record
5. **Network Failure**: Graceful degradation, retry with exponential backoff

## Performance Rules

**Timing**:
- Fetch: Every 5–10 minutes
- Processing: Instant (async)
- Storage: Async writes

**Limits**:
- Max 50–100 items per cycle
- Max 5 Gemini calls per cycle (cost control)
- Max 10 HF calls per cycle

**Monitoring**:
- Log all processing times
- Alert if any cycle > 2 minutes
- Track API costs and rate limits

## Secret Edge: Time Decay

**Implementation**:

```python
def apply_time_decay(timestamp):
    age_hours = (datetime.now(timezone.utc) - timestamp).total_seconds() / 3600
    
    if age_hours > 24:
        return None  # Ignore
    elif age_hours > 6:
        return importance_score * 0.5  # Reduce by 50%
    else:
        return importance_score  # Full score
```

**Rationale**: Old news is less actionable. News > 24 hours old is ignored entirely.

## Implementation Roadmap

1. **Phase 1**: Collector + Raw Storage (GNews + snscrape)
2. **Phase 2**: Cleaning + Deduplication + Relevance Filter
3. **Phase 3**: Importance Scoring + AI Router
4. **Phase 4**: Sentiment Engine (HF + Gemini)
5. **Phase 5**: Final Storage + Dashboard Integration
6. **Phase 6**: Fail-Safe System + Monitoring

## Success Metrics

- **Latency**: < 2 minutes per cycle
- **Accuracy**: > 80% sentiment alignment with manual review
- **Reliability**: 99.5% uptime (fail-safes working)
- **Cost**: < $10/month (Gemini + API calls)
- **Data Quality**: < 5% duplicates, < 10% irrelevant
