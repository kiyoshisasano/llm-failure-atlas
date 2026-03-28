"""
redis_help_demo_adapter.py

Adapter for the Redis movie-recommender-rag-semantic-cache-workshop
Help Center endpoint (/api/help/chat).

This adapter is specific to the workshop demo's response format.
It is NOT a general-purpose Redis adapter. Other Redis applications
will have different response structures and require their own adapters.

Converts the /api/help/chat response into Atlas matcher input format.

Workshop repository:
    https://github.com/redis-developer/movie-recommender-rag-semantic-cache-workshop

Usage:
    from adapters.redis_help_demo_adapter import RedisHelpDemoAdapter

    adapter = RedisHelpDemoAdapter()
    matcher_input = adapter.build_matcher_input(api_response)

Input format (from /api/help/chat):
    {
        "answer": str,
        "sources": [{"content": str, "similarity": float, ...}, ...],
        "from_cache": bool,
        "cache_similarity": float | None,
        "response_time_ms": int,
        "token_usage": {"prompt_tokens": int, ...} | None,
        "blocked": bool,
    }
"""

from adapters.base_adapter import BaseAdapter


# Markers indicating the LLM acknowledged uncertainty in its response
UNCERTAINTY_MARKERS = [
    "couldn't find", "could not find",
    "unable to find", "unable to retrieve",
    "no results", "no relevant results",
    "i don't have", "i do not have",
    "not sure", "i'm not certain",
    "based on general", "based on available",
    "may not be accurate", "may not be current",
    "recommend checking", "for the most accurate",
    "approximately", "estimated",
]


class RedisHelpDemoAdapter(BaseAdapter):
    """Adapter for the Redis workshop Help Center (/api/help/chat) responses.

    Specific to: movie-recommender-rag-semantic-cache-workshop.
    Not a general-purpose Redis adapter.
    """

    source = "redis_help_demo"

    def normalize(self, raw_log: dict) -> dict:
        """Pass through — Redis demo response is already structured."""
        return raw_log

    def extract_features(self, normalized: dict) -> dict:
        """Convert Redis demo response to Atlas matcher input format.

        Produces all sections that matcher YAML patterns reference.
        Missing fields default to safe values (missing_field_default=False
        in all current patterns).
        """
        sources = normalized.get("sources", [])
        answer = normalized.get("answer", "")
        from_cache = normalized.get("from_cache", False)
        cache_similarity = normalized.get("cache_similarity")
        blocked = normalized.get("blocked", False)

        # Source data analysis
        source_texts = []
        if isinstance(sources, list):
            for s in sources:
                if isinstance(s, dict):
                    source_texts.append(s.get("content", ""))
        source_data_length = sum(len(t) for t in source_texts)
        response_length = len(answer)

        if source_data_length > 0:
            expansion_ratio = round(response_length / source_data_length, 2)
        else:
            expansion_ratio = 0.0

        # Uncertainty detection in answer
        answer_lower = answer.lower()
        uncertainty_acknowledged = any(
            m in answer_lower for m in UNCERTAINTY_MARKERS
        )

        # Best source similarity (for retrieval quality assessment)
        best_similarity = 0.0
        if isinstance(sources, list):
            for s in sources:
                if isinstance(s, dict):
                    sim = s.get("similarity", 0.0)
                    if isinstance(sim, (int, float)) and sim > best_similarity:
                        best_similarity = sim

        # Blocked queries are guardrail-normal, not failures.
        # Mark progress_made=True and retrieval.skipped=True to prevent
        # false failure detection.
        if blocked:
            return {
                "input": {"ambiguity_score": 0.3},
                "interaction": {
                    "clarification_triggered": False,
                    "user_correction_detected": False,
                },
                "reasoning": {"replanned": False, "hypothesis_count": 1},
                "cache": {
                    "hit": False,
                    "similarity": 0.0,
                    "query_intent_similarity": 1.0,
                },
                "retrieval": {"skipped": True},
                "response": {"alignment_score": 1.0},
                "tools": {
                    "call_count": 0, "repeat_count": 0,
                    "unique_tools": 0, "error_count": 0,
                    "soft_error_count": 0,
                },
                "state": {"progress_made": True},
                "grounding": {
                    "tool_provided_data": False,
                    "uncertainty_acknowledged": True,
                    "response_length": response_length,
                    "source_data_length": 0,
                    "expansion_ratio": 0.0,
                },
                "meta": {"blocked_by_guardrail": True},
            }

        return {
            "input": {
                "ambiguity_score": 0.3,  # default, not inferable from response
            },
            "interaction": {
                "clarification_triggered": False,
                "user_correction_detected": False,
            },
            "reasoning": {
                "replanned": False,
                "hypothesis_count": 1,
            },
            "cache": {
                "hit": from_cache,
                "similarity": cache_similarity if cache_similarity is not None else 0.0,
                "query_intent_similarity": (
                    best_similarity if not from_cache else
                    cache_similarity if cache_similarity is not None else 0.0
                ),
            },
            "retrieval": {
                "skipped": from_cache or len(source_texts) == 0,
            },
            "response": {
                "alignment_score": best_similarity if best_similarity > 0 else 0.5,
            },
            "tools": {
                "call_count": 1 if source_texts else 0,
                "repeat_count": 0,
                "unique_tools": 1 if source_texts else 0,
                "error_count": 0,
                "soft_error_count": 0,
            },
            "state": {
                "progress_made": len(source_texts) > 0 or from_cache,
            },
            "grounding": {
                "tool_provided_data": len(source_texts) > 0,
                "uncertainty_acknowledged": uncertainty_acknowledged,
                "response_length": response_length,
                "source_data_length": source_data_length,
                "expansion_ratio": expansion_ratio,
            },
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python redis_help_demo_adapter.py <response.json>")
        print("       python redis_help_demo_adapter.py -  (read from stdin)")
        sys.exit(1)

    if sys.argv[1] == "-":
        raw = json.load(sys.stdin)
    else:
        with open(sys.argv[1], encoding="utf-8") as f:
            raw = json.load(f)

    adapter = RedisHelpDemoAdapter()
    result = adapter.build_matcher_input(raw)
    print(json.dumps(result, indent=2))