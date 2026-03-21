"""
langchain_adapter.py

Phase 24: LangChain / LangSmith trace → matcher input adapter.

Converts LangChain trace JSON into the telemetry format that matcher.py expects.

3-tier extraction:
  Tier 1: Deterministic (direct field mapping from trace)
  Tier 2: Computed (heuristic scoring from trace structure)
  Tier 3: LLM-assisted (optional, for ambiguous signals)

Usage:
  python adapters/langchain_adapter.py raw_trace.json > matcher_input.json

  from adapters.langchain_adapter import LangChainAdapter
  adapter = LangChainAdapter()
  matcher_input = adapter.build_matcher_input(raw_trace)
"""

import json
import sys
from collections import Counter
from pathlib import Path

# Handle import from both inside adapters/ and from repo root
try:
    from base_adapter import BaseAdapter
except ImportError:
    from adapters.base_adapter import BaseAdapter


class LangChainAdapter(BaseAdapter):
    """
    Adapter for LangChain / LangSmith trace format.

    Expected input: a trace dict with:
      - inputs.query (user request)
      - outputs.response (agent response)
      - steps[] (list of execution steps)
      - feedback (optional user feedback)

    Each step has:
      - type: "llm" | "retriever" | "tool"
      - name, inputs, outputs, metadata, error
    """

    source = "langchain"

    def normalize(self, raw_log: dict) -> dict:
        """
        Extract structured sections from LangChain trace.
        """
        steps = raw_log.get("steps", [])

        # Separate steps by type
        llm_steps = [s for s in steps if s.get("type") == "llm"]
        retriever_steps = [s for s in steps if s.get("type") == "retriever"]
        tool_steps = [s for s in steps if s.get("type") == "tool"]

        return {
            "query": raw_log.get("inputs", {}).get("query", ""),
            "response": raw_log.get("outputs", {}).get("response", ""),
            "llm_steps": llm_steps,
            "retriever_steps": retriever_steps,
            "tool_steps": tool_steps,
            "feedback": raw_log.get("feedback", {}),
            "latency_ms": raw_log.get("latency_ms", 0),
            "raw": raw_log,
        }

    def extract_features(self, normalized: dict) -> dict:
        """
        Build matcher-compatible telemetry from normalized trace.
        """
        return {
            "input": self._extract_input(normalized),
            "interaction": self._extract_interaction(normalized),
            "reasoning": self._extract_reasoning(normalized),
            "cache": self._extract_cache(normalized),
            "retrieval": self._extract_retrieval(normalized),
            "response": self._extract_response(normalized),
            "tools": self._extract_tools(normalized),
        }

    # ----- Tier 1: Deterministic extraction -----

    def _extract_cache(self, normalized: dict) -> dict:
        """Extract cache info from retriever steps."""
        for step in normalized["retriever_steps"]:
            meta = step.get("metadata", {})
            if meta.get("cache_hit") is not None:
                return {
                    "hit": bool(meta.get("cache_hit", False)),
                    "similarity": float(meta.get("cache_similarity", 0.0)),
                    "query_intent_similarity": self._compute_intent_similarity(normalized),
                }
        return {
            "hit": False,
            "similarity": 0.0,
            "query_intent_similarity": 1.0,
        }

    def _extract_retrieval(self, normalized: dict) -> dict:
        """Extract retrieval state."""
        for step in normalized["retriever_steps"]:
            meta = step.get("metadata", {})
            return {
                "skipped": bool(meta.get("retrieval_skipped", False)),
            }
        # No retriever step = retrieval was skipped
        if not normalized["retriever_steps"]:
            return {"skipped": True}
        return {"skipped": False}

    def _extract_tools(self, normalized: dict) -> dict:
        """Extract tool call patterns."""
        tool_steps = normalized["tool_steps"]
        call_count = len(tool_steps)

        # Detect repeated calls (same tool name, same inputs)
        calls = [(s["name"], json.dumps(s.get("inputs", {}), sort_keys=True))
                 for s in tool_steps]
        call_counts = Counter(calls)
        max_repeat = max(call_counts.values()) if call_counts else 0
        repeat_count = max_repeat - 1 if max_repeat > 1 else 0

        # Detect errors
        error_count = sum(1 for s in tool_steps if s.get("error"))

        return {
            "call_count": call_count,
            "repeat_count": repeat_count,
            "unique_tools": len(set(s["name"] for s in tool_steps)) if tool_steps else 0,
            "error_count": error_count,
        }

    def _extract_interaction(self, normalized: dict) -> dict:
        """Extract interaction signals."""
        feedback = normalized.get("feedback", {})
        user_correction = feedback.get("user_correction", "")

        # Check if any LLM step asked a clarification question
        clarification = False
        for step in normalized["llm_steps"]:
            output = step.get("outputs", {}).get("text", "")
            if any(marker in output.lower() for marker in
                   ["could you clarify", "did you mean", "can you specify",
                    "what do you mean", "which one", "please clarify"]):
                clarification = True
                break

        return {
            "clarification_triggered": clarification,
            "user_correction_detected": bool(user_correction),
        }

    # ----- Tier 2: Computed features -----

    def _extract_input(self, normalized: dict) -> dict:
        """Estimate input ambiguity (heuristic)."""
        query = normalized.get("query", "")
        score = self._estimate_ambiguity(query)
        return {"ambiguity_score": score}

    def _extract_reasoning(self, normalized: dict) -> dict:
        """Detect replanning from LLM step patterns."""
        llm_steps = normalized["llm_steps"]
        replanned = False

        # Heuristic: if there are 2+ LLM calls and the second mentions
        # correction/retry/different approach, consider it replanning
        if len(llm_steps) >= 2:
            for step in llm_steps[1:]:
                output = step.get("outputs", {}).get("text", "")
                if any(word in output.lower() for word in
                       ["let me try", "actually", "correction",
                        "different approach", "reconsider"]):
                    replanned = True
                    break

        return {"replanned": replanned}

    def _extract_response(self, normalized: dict) -> dict:
        """Estimate response alignment with query (heuristic)."""
        query = normalized.get("query", "")
        response = normalized.get("response", "")
        score = self._estimate_alignment(query, response)
        return {"alignment_score": score}

    def _estimate_ambiguity(self, query: str) -> float:
        """
        Tier 2: Heuristic ambiguity estimation.
        Higher = more ambiguous.
        """
        if not query:
            return 0.5

        score = 0.3  # baseline

        # Short queries tend to be more ambiguous
        words = query.split()
        if len(words) <= 3:
            score += 0.2
        elif len(words) <= 6:
            score += 0.1

        # Questions with "it", "that", "this" without clear referent
        ambiguous_pronouns = {"it", "that", "this", "they", "them"}
        if any(w.lower() in ambiguous_pronouns for w in words):
            score += 0.15

        # Multiple possible intents (contains "or", "maybe", "either")
        if any(w.lower() in {"or", "maybe", "either", "perhaps"} for w in words):
            score += 0.15

        return min(1.0, round(score, 2))

    def _compute_intent_similarity(self, normalized: dict) -> float:
        """
        Tier 2: Heuristic intent similarity between query and retrieved docs.
        Lower = more mismatch.
        """
        query = normalized.get("query", "").lower()
        if not query:
            return 1.0

        query_words = set(query.split())
        if not query_words:
            return 1.0

        # Check retrieved documents for keyword overlap
        best_overlap = 0.0
        for step in normalized["retriever_steps"]:
            docs = step.get("outputs", {}).get("documents", [])
            for doc in docs:
                content = doc.get("content", "").lower()
                doc_words = set(content.split())
                if doc_words:
                    overlap = len(query_words & doc_words) / len(query_words)
                    best_overlap = max(best_overlap, overlap)

        return round(best_overlap, 2)

    def _estimate_alignment(self, query: str, response: str) -> float:
        """
        Tier 2: Heuristic alignment between query intent and response.
        Higher = better aligned.
        """
        if not query or not response:
            return 0.5

        query_words = set(query.lower().split())
        response_words = set(response.lower().split())

        if not query_words:
            return 0.5

        # Simple keyword overlap as proxy for alignment
        overlap = len(query_words & response_words) / len(query_words)

        return round(min(1.0, overlap), 2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not args:
        print("Usage: python langchain_adapter.py raw_trace.json [--with-metadata]")
        sys.exit(1)

    with open(args[0]) as f:
        raw_log = json.load(f)

    adapter = LangChainAdapter()

    if "--with-metadata" in sys.argv:
        result = adapter.build_with_metadata(raw_log)
    else:
        result = adapter.build_matcher_input(raw_log)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
