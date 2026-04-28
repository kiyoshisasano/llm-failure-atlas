"""
base_adapter.py

Phase 24: Base adapter interface for log → matcher input conversion.

Adapters convert raw agent logs (LangChain, LangSmith, AgentOps, etc.)
into the telemetry format that matcher.py expects.

Adapters do NOT diagnose failures — they only normalize and extract features.

3-tier signal extraction:
  Tier 1: Deterministic extraction (direct field mapping)
  Tier 2: Computed features (embeddings, scores)
  Tier 3: LLM-assisted inference (optional, for ambiguous signals)
"""

import re

# Common English stop words. Kept short and conservative — over-aggressive
# filtering would discard legitimate query terms.
_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "for",
    "of", "in", "on", "at", "to", "from", "by", "with", "as", "is",
    "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may",
    "might", "can", "this", "that", "these", "those", "it", "its",
    "they", "them", "their", "there", "where", "when", "what", "which",
    "who", "whom", "how", "why", "not", "no", "nor", "so", "than",
    "too", "very", "just", "also", "only", "into", "out", "up", "down",
    "any", "all", "some", "such", "more", "most", "other", "much",
    "many", "few", "own", "same", "each", "both", "either", "neither",
})


def _tokenize_distinctive(text: str) -> set:
    """
    Tokenize text into distinctive tokens used for chunk-utilisation overlap.

    Distinctive tokens are words 4 characters or longer (lowercase),
    minus common stop words. Numeric tokens of any length are kept
    (numbers are highly discriminative for grounding).

    Returns a set (order/duplicates do not matter for overlap ratio).

    This is a deterministic proxy and not a semantic similarity measure.
    See compute_chunk_utilisation() for the documented limitations.
    """
    if not text or not isinstance(text, str):
        return set()

    # Word-token extraction: alphanumerics and underscores
    raw_tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())

    distinctive = set()
    for tok in raw_tokens:
        if not tok:
            continue
        # Keep all numeric tokens (1, 22, 12.4M loses the dot but '12' '4m' survive)
        if any(ch.isdigit() for ch in tok):
            distinctive.add(tok)
            continue
        # Drop short alpha tokens and stop words
        if len(tok) < 4:
            continue
        if tok in _STOP_WORDS:
            continue
        distinctive.add(tok)
    return distinctive


def compute_chunk_utilisation(chunks: list, response: str,
                              threshold: float = 0.30) -> dict:
    """
    Approximate which retrieved chunks were used by the agent's response.

    This is a PROXY measurement — text overlap between chunk content and
    final response. It does not observe what the agent "actually used"
    semantically. False positives occur when a chunk shares incidental
    vocabulary with the response without contributing to it. False
    negatives occur when the agent paraphrases content heavily.

    The proxy is documented as such (see method='text_overlap_proxy' in
    downstream consumers) so callers know not to treat the output as
    ground truth.

    Args:
        chunks: list of dicts with 'chunk_id' and 'content' keys.
            Other shapes (with 'page_content', 'text', etc.) are
            tolerated by callers — chunks should be pre-normalized.
        response: agent's final response string.
        threshold: minimum overlap ratio (chunk-token-side) to count
            a chunk as 'used'. Default 0.30.

    Returns:
        {
            "used_chunk_ids": [chunk_id, ...],   # subset of chunks
            "method": "text_overlap_proxy",
            "threshold": 0.30,
        }
        If chunks is empty or response is empty/missing, returns:
        {
            "used_chunk_ids": None,
            "method": "text_overlap_proxy",
            "threshold": 0.30,
        }
    """
    if not chunks or not response:
        return {
            "used_chunk_ids": None,
            "method": "text_overlap_proxy",
            "threshold": threshold,
        }

    response_tokens = _tokenize_distinctive(response)
    if not response_tokens:
        return {
            "used_chunk_ids": None,
            "method": "text_overlap_proxy",
            "threshold": threshold,
        }

    used = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_id = chunk.get("chunk_id")
        if not chunk_id:
            continue
        content = chunk.get("content") or chunk.get("page_content") \
                  or chunk.get("text") or ""
        chunk_tokens = _tokenize_distinctive(content)
        if not chunk_tokens:
            continue
        overlap = len(chunk_tokens & response_tokens)
        ratio = overlap / len(chunk_tokens)
        if ratio >= threshold:
            used.append(chunk_id)

    return {
        "used_chunk_ids": used if used else None,
        "method": "text_overlap_proxy",
        "threshold": threshold,
    }


class BaseAdapter:
    """
    Abstract base for all log adapters.

    Subclasses must implement:
      normalize()        — raw log → canonical structure
      extract_features() — canonical → matcher-compatible telemetry

    Usage:
        adapter = SomeAdapter()
        matcher_input = adapter.build_matcher_input(raw_log)
    """

    # Source identifier (override in subclass)
    source: str = "unknown"

    def normalize(self, raw_log: dict) -> dict:
        """
        Convert raw log format into a canonical intermediate structure.
        This handles vendor-specific field names and nesting.
        """
        raise NotImplementedError

    def extract_features(self, normalized: dict) -> dict:
        """
        Extract matcher-compatible telemetry from the normalized structure.

        Output must match the log format expected by matcher.py:
        {
            "input": {"ambiguity_score": ...},
            "interaction": {"clarification_triggered": ..., ...},
            "reasoning": {"replanned": ...},
            "cache": {"hit": ..., "similarity": ..., "query_intent_similarity": ...},
            "retrieval": {"skipped": ...},
            "response": {"alignment_score": ...},
            "tools": {"call_count": ..., "repeat_count": ..., ...}
        }
        """
        raise NotImplementedError

    def build_matcher_input(self, raw_log: dict) -> dict:
        """
        Full pipeline: raw log → normalized → matcher input.

        Returns the telemetry dict that matcher.py can consume directly.
        """
        normalized = self.normalize(raw_log)
        features = self.extract_features(normalized)
        return features

    def build_with_metadata(self, raw_log: dict) -> dict:
        """
        Build matcher input with source metadata attached.

        Returns:
        {
            "telemetry": {...},   # matcher-compatible
            "metadata": {
                "source": "langchain",
                "adapter_version": "1.0",
                ...
            }
        }
        """
        telemetry = self.build_matcher_input(raw_log)
        return {
            "telemetry": telemetry,
            "metadata": {
                "source": self.source,
                "adapter_version": "1.0",
            },
        }