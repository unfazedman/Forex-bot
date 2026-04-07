"""
sentiment_scanner.py - AI-Powered Sentiment Pipeline
Fusion Score Bot V6.0

Pipeline Architecture (11 layers):
    1.  Data Sources      : ForexLive RSS + GNews API
    2.  Collector         : Fetch & standardize
    3.  Raw Storage       : Store everything in Supabase (raw_sentiment_data)
    4.  Cleaning          : Remove URLs, normalize whitespace
    5.  Deduplication     : Fuzzy match at 85% threshold (fuzzywuzzy)
    6.  Relevance Filter  : Financial keyword gate
    7.  Importance Scoring: Keyword tier + time decay
    8.  AI Router         : Assigns model based on importance + daily budget
    9.  Sentiment Engine  : FinBERT (primary) → Gemini (golden ticket)
    10. Final Storage     : processed_sentiment table
    11. Aggregation       : Calls aggregate_and_push_sentiment() → updates
                           system_state.macro_sentiment → feeds Fusion Score

Key Architecture Decisions:
    - FinBERT is PRIMARY. Handles all MEDIUM and LOW importance articles.
    - Gemini is the DAILY GOLDEN TICKET. Max 1 article per run (2 calls:
      EUR + GBP). Hard cap enforced by GeminiRateLimiter.
    - RPD=20 verified from AI Studio April 2026. Public docs are wrong.
    - Dedup is fuzzy (fuzzywuzzy), not hash-only. Two headlines saying the
      same thing in different words will be caught.
    - Hash is added to processed_hashes ONLY after successful DB insert.
      The old V5 bug (pre-insert hashing) is fixed.
"""

import re
import os
import json
import time
import logging
import hashlib
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from uuid import uuid4
from fuzzywuzzy import fuzz
import telebot

from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    GEMINI_API_KEY, HUGGINGFACE_API_KEY,
    GNEWS_API_KEY,
    GEMINI_RPD_LIMIT, GEMINI_THROTTLE_DELAY, GEMINI_CALLS_PER_CYCLE,
    SIMILARITY_THRESHOLD, MAX_ITEMS_PER_CYCLE,
    IMPORTANCE_DECAY_HOURS, IMPORTANCE_CUTOFF_HOURS,
    validate_config
)
from shared_functions import (
    get_supabase_client,
    send_error_notification,
    aggregate_and_push_sentiment
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# KEYWORD SETS FOR FILTERING AND SCORING
# =============================================================================

FINANCIAL_KEYWORDS = {
    'inflation', 'interest rate', 'rate hike', 'rate cut', 'federal reserve',
    'ecb', 'european central bank', 'bank of england', 'boe', 'boj',
    'bank of japan', 'gdp', 'gross domestic product', 'employment',
    'unemployment', 'nfp', 'non-farm payroll', 'cpi', 'consumer price index',
    'ppi', 'pce', 'oil', 'crude', 'gold', 'forex', 'currency',
    'exchange rate', 'usd', 'eur', 'gbp', 'jpy',
    'powell', 'lagarde', 'bailey', 'fed', 'fomc', 'tariff', 'trade war',
    'recession', 'stagflation', 'yield', 'treasury', 'bond'
}

CENTRAL_BANK_KEYWORDS = {
    'federal reserve', 'fed', 'fomc', 'powell',
    'ecb', 'european central bank', 'lagarde',
    'bank of england', 'boe', 'bailey',
    'boj', 'bank of japan'
}

HIGH_IMPACT_KEYWORDS = {
    'inflation', 'cpi', 'gdp', 'nfp', 'non-farm payroll',
    'interest rate', 'rate hike', 'rate cut', 'fomc', 'recession'
}

# =============================================================================
# GEMINI RATE LIMITER
# Enforces the RPD=20 hard cap verified from AI Studio (April 2026).
# Public docs claim 250 RPD. Reality is 20. This class is the enforcement.
# =============================================================================

class GeminiRateLimiter:
    """
    Tracks Gemini API calls within a single pipeline run.
    Enforces GEMINI_CALLS_PER_CYCLE hard cap.
    Also enforces minimum delay between calls for RPM compliance.
    """

    def __init__(self):
        self.calls_this_cycle = 0
        self.last_call_time   = 0.0

    def can_call(self) -> bool:
        """Returns True if we're within the per-cycle budget."""
        return self.calls_this_cycle < GEMINI_CALLS_PER_CYCLE

    def wait_and_record(self):
        """
        Sleeps if needed to respect RPM limit, then records the call.
        Call this BEFORE making the actual API request.
        """
        elapsed = time.time() - self.last_call_time
        if elapsed < GEMINI_THROTTLE_DELAY:
            sleep_time = GEMINI_THROTTLE_DELAY - elapsed
            logger.info(f"[RateLimit] Sleeping {sleep_time:.1f}s for Gemini RPM...")
            time.sleep(sleep_time)

        self.calls_this_cycle += 1
        self.last_call_time   = time.time()

    def remaining(self) -> int:
        return max(0, GEMINI_CALLS_PER_CYCLE - self.calls_this_cycle)


# =============================================================================
# MAIN PIPELINE CLASS
# =============================================================================

class SentimentScannerPipeline:
    """
    Full 11-layer sentiment analysis pipeline.
    """

    def __init__(self):
        try:
            validate_config('sentiment_scanner')
            self.supabase = get_supabase_client()
            self.bot      = telebot.TeleBot(TELEGRAM_TOKEN)
            logger.info("[Scanner] Initialized successfully.")
        except Exception as e:
            logger.error(f"[Scanner] Initialization failed: {e}")
            send_error_notification(f"Scanner Init Failed: {e}")
            self.supabase = None
            self.bot      = None

        self.state_file       = 'scanner_state.json'
        self.processed_hashes = set()
        self.gemini_limiter   = GeminiRateLimiter()
        self._load_state()

    # =========================================================================
    # STATE MANAGEMENT (processed hash memory)
    # =========================================================================

    def _load_state(self):
        """Loads processed hashes from state file."""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.processed_hashes = set(state.get('processed_hashes', []))
                    logger.info(f"[State] Loaded {len(self.processed_hashes)} hashes.")
        except Exception as e:
            logger.error(f"[State] Failed to load state: {e}")
            self.processed_hashes = set()

    def _save_state(self):
        """Saves processed hashes to state file. Keeps last 1000 only."""
        try:
            hashes_list = list(self.processed_hashes)[-1000:]
            with open(self.state_file, 'w') as f:
                json.dump({'processed_hashes': hashes_list}, f)
            logger.info("[State] State saved.")
        except Exception as e:
            logger.error(f"[State] Failed to save state: {e}")

    # =========================================================================
    # LAYER 2: COLLECTORS
    # =========================================================================

    def _collect_rss(self) -> List[Dict]:
        """Fetches headlines from ForexLive RSS feed."""
        rss_url   = "https://www.forexlive.com/feed"
        headers   = {'User-Agent': 'Mozilla/5.0'}
        collected = []

        try:
            response = requests.get(rss_url, headers=headers, timeout=15)
            response.raise_for_status()
            feed = feedparser.parse(response.content)

            for entry in feed.entries:
                text      = entry.get('title', '').strip()
                text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()

                if not text or text_hash in self.processed_hashes:
                    continue

                collected.append({
                    "id":        str(uuid4()),
                    "text":      text,
                    "source":    "rss",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "author":    "ForexLive",
                    "url":       entry.get('link', ''),
                    "hash":      text_hash
                })

            logger.info(f"[Collector] RSS: {len(collected)} new items.")
        except Exception as e:
            logger.error(f"[Collector] RSS error: {e}")

        return collected

    def _collect_gnews(self) -> List[Dict]:
        """Fetches headlines from GNews API."""
        if not GNEWS_API_KEY:
            logger.warning("[Collector] GNews API key not set. Skipping.")
            return []

        keywords  = ["inflation Fed", "ECB rate", "GDP employment"]
        collected = []

        for keyword in keywords:
            try:
                url      = f"https://gnews.io/api/v4/search?q={keyword}&token={GNEWS_API_KEY}&lang=en&max=5"
                response = requests.get(url, timeout=15)
                response.raise_for_status()
                data     = response.json()

                for article in data.get('articles', []):
                    text      = article.get('title', '').strip()
                    text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()

                    if not text or text_hash in self.processed_hashes:
                        continue

                    collected.append({
                        "id":        str(uuid4()),
                        "text":      text,
                        "source":    "news",
                        "timestamp": article.get(
                            'publishedAt',
                            datetime.now(timezone.utc).isoformat()
                        ),
                        "author":    article.get('source', {}).get('name', 'GNews'),
                        "url":       article.get('url', ''),
                        "hash":      text_hash
                    })

            except Exception as e:
                logger.error(f"[Collector] GNews error for '{keyword}': {e}")

        logger.info(f"[Collector] GNews: {len(collected)} new items.")
        return collected

    # =========================================================================
    # LAYER 3: RAW STORAGE
    # =========================================================================

    def _store_raw(self, items: List[Dict]):
        """Stores all collected items to raw_sentiment_data before filtering."""
        if not self.supabase or not items:
            return

        try:
            records = [{
                "id":        item['id'],
                "text":      item['text'],
                "source":    item['source'],
                "timestamp": item['timestamp'],
                "author":    item['author'],
                "url":       item['url']
            } for item in items]

            self.supabase.table("raw_sentiment_data").insert(records).execute()
            logger.info(f"[RawStorage] Stored {len(records)} raw items.")
        except Exception as e:
            logger.error(f"[RawStorage] Failed: {e}")

    # =========================================================================
    # LAYER 4: CLEANING
    # =========================================================================

    @staticmethod
    def _clean_text(text: str) -> Optional[str]:
        """
        Cleans text: removes URLs, normalizes whitespace.
        Returns None if text is too short after cleaning.
        """
        if not text:
            return None

        # Remove URLs
        text = re.sub(r'http\S+|www\S+|https\S+', '', text)
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        # Reject very short strings (not useful for sentiment)
        if len(text) < 20:
            return None

        return text[:500]

    # =========================================================================
    # LAYER 5: DEDUPLICATION (Fuzzy — not hash-only)
    # =========================================================================

    def _deduplicate(self, items: List[Dict]) -> List[Dict]:
        """
        Removes near-duplicate headlines using fuzzy string matching.
        Threshold: SIMILARITY_THRESHOLD (0.85 = 85%).

        Two headlines saying the same thing in different words are caught
        here. Hash-only dedup (V5) would miss these.

        Keeps the first occurrence when duplicates are found.
        """
        unique = []

        for item in items:
            is_duplicate = False
            for seen in unique:
                similarity = fuzz.ratio(
                    item['text'].lower(),
                    seen['text'].lower()
                ) / 100.0

                if similarity >= SIMILARITY_THRESHOLD:
                    is_duplicate = True
                    logger.info(
                        f"[Dedup] Skipping duplicate ({similarity:.0%}): "
                        f"{item['text'][:60]}..."
                    )
                    break

            if not is_duplicate:
                unique.append(item)

        logger.info(f"[Dedup] {len(items)} → {len(unique)} after dedup.")
        return unique

    # =========================================================================
    # LAYER 6: RELEVANCE FILTER
    # =========================================================================

    @staticmethod
    def _is_relevant(text: str) -> bool:
        """Checks if text contains financial keywords."""
        text_lower = text.lower()
        return any(kw in text_lower for kw in FINANCIAL_KEYWORDS)

    # =========================================================================
    # LAYER 7: IMPORTANCE SCORING
    # =========================================================================

    def _calculate_importance(self, text: str, timestamp: str) -> Dict:
        """
        Scores article importance based on keyword tier and time decay.

        Scoring:
            Central bank keywords  → base +3.0
            High impact keywords   → base +2.0
            Other financial        → base +1.0
            Age > IMPORTANCE_CUTOFF_HOURS (24h) → EXPIRED (skip)
            Age > IMPORTANCE_DECAY_HOURS  (6h)  → score × 0.5

        Tiers:
            HIGH   (≥ 4.0): Central bank statements, major data
            MEDIUM (≥ 2.0): Inflation reports, economic data
            LOW    (< 2.0): Minor news
        """
        text_lower = text.lower()
        base_score = 0.5

        if any(kw in text_lower for kw in CENTRAL_BANK_KEYWORDS):
            base_score += 3.0
        elif any(kw in text_lower for kw in HIGH_IMPACT_KEYWORDS):
            base_score += 2.0
        else:
            base_score += 1.0

        # Time decay
        try:
            item_time = datetime.fromisoformat(
                timestamp.replace('Z', '+00:00')
            )
            now       = datetime.now(timezone.utc)
            age_hours = (now - item_time).total_seconds() / 3600

            if age_hours > IMPORTANCE_CUTOFF_HOURS:
                return {"score": 0.0, "tier": "EXPIRED"}
            elif age_hours > IMPORTANCE_DECAY_HOURS:
                base_score *= 0.5
                logger.info(
                    f"[Importance] Time decay applied ({age_hours:.1f}h old)."
                )
        except Exception:
            pass  # Unparseable timestamp — use full score

        tier = (
            "HIGH"   if base_score >= 4.0 else
            "MEDIUM" if base_score >= 2.0 else
            "LOW"
        )

        return {"score": round(base_score, 2), "tier": tier}

    # =========================================================================
    # LAYER 8: AI ROUTER
    # =========================================================================

    def _assign_model(self, importance_tier: str) -> str:
        """
        Routes each article to the appropriate model.

        Logic:
            HIGH importance + Gemini budget remaining → Gemini
            Everything else                           → HuggingFace

        Gemini budget is enforced by GeminiRateLimiter.
        Only 1 article per run gets Gemini (2 calls: EUR + GBP).
        """
        if importance_tier == "HIGH" and self.gemini_limiter.can_call():
            return "Gemini"
        return "HuggingFace"

    # =========================================================================
    # LAYER 9: SENTIMENT ENGINE
    # =========================================================================

    def _analyze_with_huggingface(self, text: str) -> Dict:
        """
        Primary sentiment model: ProsusAI/FinBERT.
        Fine-tuned on financial text — institutional standard.

        Returns BULLISH / BEARISH / NEUTRAL with confidence score.
        Note: FinBERT is pair-agnostic. "Fed hikes rates" is BEARISH
        regardless of pair. This is correct for macro news.

        Handles HuggingFace cold-start (model loading) response gracefully.
        """
        if not HUGGINGFACE_API_KEY:
            logger.warning("[FinBERT] API key not set. Returning NEUTRAL.")
            return {"sentiment": "NEUTRAL", "confidence": 0.0, "model": "HuggingFace-FinBERT"}

        api_url = "https://router.huggingface.co/hf-inference/models/ProsusAI/finbert"
        headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}

        try:
            response = requests.post(
                api_url,
                headers=headers,
                json={"inputs": text[:512]},
                timeout=15
            )
            response.raise_for_status()
            result = response.json()

            # Handle cold-start: HuggingFace returns a dict, not a list
            if isinstance(result, dict) and 'error' in result:
                estimated = result.get('estimated_time', '?')
                logger.warning(
                    f"[FinBERT] Model loading. Estimated time: {estimated}s. "
                    f"Returning NEUTRAL."
                )
                return {"sentiment": "NEUTRAL", "confidence": 0.0, "model": "HuggingFace-FinBERT"}

            # Normal response: [[{label, score}, ...]]
            if not isinstance(result, list) or not result or not isinstance(result[0], list):
                logger.error(f"[FinBERT] Unexpected response format: {result}")
                return {"sentiment": "NEUTRAL", "confidence": 0.0, "model": "HuggingFace-FinBERT"}

            top        = result[0][0]
            label      = top.get('label', '').lower()
            confidence = float(top.get('score', 0.0))

            sentiment_map = {
                "positive": "BULLISH",
                "negative": "BEARISH",
                "neutral":  "NEUTRAL"
            }
            sentiment = sentiment_map.get(label, "NEUTRAL")

            logger.info(f"[FinBERT] {sentiment} ({confidence:.2f}): {text[:60]}...")
            return {"sentiment": sentiment, "confidence": confidence, "model": "HuggingFace-FinBERT"}

        except Exception as e:
            logger.error(f"[FinBERT] Error: {e}")
            return {"sentiment": "NEUTRAL", "confidence": 0.0, "model": "HuggingFace-FinBERT"}

    def _analyze_with_gemini(self, text: str, pair: str) -> Dict:
        """
        Golden ticket model: Gemini 2.5 Flash.
        Used ONLY for HIGH importance articles, max 1 article per run.
        Pair-specific: asks about EUR/USD or GBP/USD impact separately.

        Rate limiting enforced by GeminiRateLimiter before each call.
        """
        if not GEMINI_API_KEY:
            logger.warning("[Gemini] API key not set. Falling back to FinBERT.")
            return self._analyze_with_huggingface(text)

        # Enforce RPM delay
        self.gemini_limiter.wait_and_record()

        prompt = (
            f"Analyze this financial news for its impact on {pair} price "
            f"over the next 2 hours.\n"
            f"Output ONLY valid JSON, no markdown:\n"
            f'{{"sentiment": "Bullish|Bearish|Neutral", "confidence": 0.0-1.0}}\n\n'
            f'News: "{text[:500]}"'
        )

        try:
            url     = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
            )
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2}
            }

            response = requests.post(url, json=payload, timeout=20)
            response.raise_for_status()

            raw_text   = (
                response.json()
                ['candidates'][0]['content']['parts'][0]['text']
                .strip()
            )
            json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)

            if json_match:
                result     = json.loads(json_match.group())
                sentiment  = result.get('sentiment', 'NEUTRAL').upper()
                confidence = float(result.get('confidence', 0.0))

                # Normalize: Gemini might return "Bullish" not "BULLISH"
                if sentiment not in {"BULLISH", "BEARISH", "NEUTRAL"}:
                    sentiment = "NEUTRAL"

                logger.info(
                    f"[Gemini] {pair}: {sentiment} ({confidence:.2f}): "
                    f"{text[:60]}..."
                )
                return {"sentiment": sentiment, "confidence": confidence, "model": "Gemini"}

            logger.error(f"[Gemini] No JSON found in response: {raw_text[:100]}")

        except Exception as e:
            logger.error(f"[Gemini] Error: {e}. Falling back to FinBERT.")

        # Fallback to FinBERT if Gemini fails
        return self._analyze_with_huggingface(text)

    def _analyze_sentiment(self, text: str, pair: str, model_assigned: str) -> Dict:
        """
        Dispatcher: routes to correct model based on AI Router assignment.
        For HuggingFace, pair is ignored (pair-agnostic model).
        For Gemini, pair is passed for pair-specific prompting.
        """
        if model_assigned == "Gemini":
            return self._analyze_with_gemini(text, pair)
        else:
            return self._analyze_with_huggingface(text)

    # =========================================================================
    # LAYERS 10 + 11: STORAGE AND AGGREGATION
    # =========================================================================

    def _store_processed(self, item: Dict, importance: Dict,
                         eur_sent: Dict, gbp_sent: Dict,
                         model_assigned: str) -> bool:
        """
        Stores processed sentiment to Supabase.
        Returns True on success, False on failure.
        Hash is added to processed_hashes ONLY on success.
        (Fixes the V5 bug where hash was added before DB write succeeded.)
        """
        if not self.supabase:
            return False

        try:
            self.supabase.table("processed_sentiment").insert({
                "text_cleaned":       item['text_cleaned'],
                "source":             item['source'],
                "timestamp":          item['timestamp'],
                "importance_score":   importance['score'],
                "importance_tier":    importance['tier'],
                "eur_usd_sentiment":  eur_sent['sentiment'],
                "eur_usd_confidence": eur_sent['confidence'],
                "gbp_usd_sentiment":  gbp_sent['sentiment'],
                "gbp_usd_confidence": gbp_sent['confidence'],
                "model_used":         model_assigned
            }).execute()

            # Only mark as processed after confirmed DB write
            self.processed_hashes.add(item['hash'])
            return True

        except Exception as e:
            logger.error(f"[Storage] DB insert failed: {e}")
            return False

    # =========================================================================
    # MAIN ORCHESTRATOR
    # =========================================================================

    def run_pipeline(self) -> Dict:
        """
        Runs the full 11-layer pipeline.

        Returns:
            Dict with status, processed count, and sentiment summary.
        """
        logger.info("[Pipeline] ===== Sentiment Pipeline Starting =====")

        # ---- LAYER 2: Collect ----
        raw_items = self._collect_rss() + self._collect_gnews()
        raw_items = raw_items[:MAX_ITEMS_PER_CYCLE]

        if not raw_items:
            logger.info("[Pipeline] No new items collected.")
            return {"status": "success", "processed": 0}

        # ---- LAYER 3: Raw Storage ----
        self._store_raw(raw_items)

        # ---- LAYER 4: Clean ----
        for item in raw_items:
            item['text_cleaned'] = self._clean_text(item['text'])

        cleaned_items = [i for i in raw_items if i['text_cleaned']]

        # ---- LAYER 5: Deduplicate (fuzzy) ----
        deduped_items = self._deduplicate(cleaned_items)

        # ---- LAYER 6: Relevance Filter ----
        relevant_items = [
            i for i in deduped_items
            if self._is_relevant(i['text_cleaned'])
        ]
        logger.info(f"[Filter] {len(relevant_items)} relevant items after filter.")

        # ---- LAYER 7: Importance Scoring ----
        scored_items = []
        for item in relevant_items:
            importance = self._calculate_importance(
                item['text_cleaned'], item['timestamp']
            )
            if importance['tier'] == "EXPIRED":
                logger.info(f"[Importance] Skipping expired item: {item['text'][:60]}")
                continue
            item['importance'] = importance
            scored_items.append(item)

        # Sort HIGH → MEDIUM → LOW so Gemini sees the best article first
        tier_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        scored_items.sort(key=lambda x: tier_order.get(x['importance']['tier'], 3))

        logger.info(f"[Pipeline] {len(scored_items)} items to process.")

        # ---- LAYERS 8 + 9 + 10: Route → Analyse → Store ----
        processed_count = 0
        eur_results     = []
        gbp_results     = []

        for item in scored_items:
            importance     = item['importance']
            model_assigned = self._assign_model(importance['tier'])

            logger.info(
                f"[Router] [{importance['tier']}] → {model_assigned}: "
                f"{item['text_cleaned'][:60]}..."
            )

            # Analyse for each pair
            eur_sent = self._analyze_sentiment(
                item['text_cleaned'], "EUR/USD", model_assigned
            )

            # For HuggingFace: result is pair-agnostic, reuse for GBP
            # For Gemini: make a separate pair-specific call
            if model_assigned == "Gemini":
                gbp_sent = self._analyze_sentiment(
                    item['text_cleaned'], "GBP/USD", model_assigned
                )
            else:
                # FinBERT is pair-agnostic — same result applies to both pairs
                gbp_sent = eur_sent.copy()

            # Store to DB (hash only added on success)
            success = self._store_processed(
                item, importance, eur_sent, gbp_sent, model_assigned
            )

            if success:
                processed_count += 1
                eur_results.append(eur_sent['sentiment'])
                gbp_results.append(gbp_sent['sentiment'])

        # ---- LAYER 11: Aggregation → system_state ----
        # This is the step that was missing in V5.
        # Without this, macro_sentiment stays 0 forever.
        if processed_count > 0:
            logger.info("[Pipeline] Running sentiment aggregation...")
            aggregate_and_push_sentiment("EUR/USD")
            aggregate_and_push_sentiment("GBP/USD")

        # Save state (processed hashes)
        self._save_state()

        # Summary log
        gemini_used = self.gemini_limiter.calls_this_cycle
        logger.info(
            f"[Pipeline] Complete. Processed: {processed_count} | "
            f"Gemini calls: {gemini_used}/{GEMINI_CALLS_PER_CYCLE}"
        )
        logger.info("[Pipeline] ===== Pipeline Complete =====")

        return {
            "status":         "success",
            "processed":      processed_count,
            "gemini_calls":   gemini_used,
            "eur_results":    eur_results,
            "gbp_results":    gbp_results
        }


if __name__ == "__main__":
    pipeline = SentimentScannerPipeline()
    pipeline.run_pipeline()
