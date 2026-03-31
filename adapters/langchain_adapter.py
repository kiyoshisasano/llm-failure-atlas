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
            "state": self._extract_state(normalized),
            "grounding": self._extract_grounding(normalized),
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

    # Markers for tool outputs that indicate a soft failure (no usable data).
    TOOL_SOFT_ERROR_MARKERS = [
        "error", "unavailable", "service unavailable",
        "could not", "failed to", "exception",
        "no results", "0 results", "0 matching",
        "not found", "no data", "no records",
        "empty", "none found", "[]",
    ]

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

        # Hard errors
        error_count = sum(1 for s in tool_steps if s.get("error"))

        # Soft errors: tool returned successfully but output contains
        # error/empty markers
        soft_error_count = 0
        for s in tool_steps:
            if s.get("error"):
                continue
            output = json.dumps(s.get("outputs", {})).lower()
            if any(m in output for m in self.TOOL_SOFT_ERROR_MARKERS):
                soft_error_count += 1

        return {
            "call_count": call_count,
            "repeat_count": repeat_count,
            "unique_tools": len(set(s["name"] for s in tool_steps)) if tool_steps else 0,
            "error_count": error_count,
            "soft_error_count": soft_error_count,
        }

    def _extract_interaction(self, normalized: dict) -> dict:
        """Extract interaction signals."""
        feedback = normalized.get("feedback", {})
        user_correction = feedback.get("user_correction", "")

        # Markers that indicate the agent is requesting clarification.
        # Two groups: direct clarification questions and information
        # requests that implicitly seek clarification (common in Claude).
        clarification_markers = [
            # Direct clarification
            "could you clarify", "did you mean", "can you specify",
            "what do you mean", "please clarify", "which one",
            # Information requests (Claude-style clarification)
            "could you provide", "could you please provide",
            "can you provide", "i need the", "i need to know",
            "what is the", "what is your", "which ",
            "please provide", "please specify",
        ]
        clarification = False
        for step in normalized["llm_steps"]:
            output = step.get("outputs", {}).get("text", "")
            if any(marker in output.lower() for marker in
                   clarification_markers):
                clarification = True
                break

        # Infer user_correction_detected from response content when
        # no explicit feedback is available. Detects topic pivot:
        # response admits failure on the original task AND pivots
        # to a different topic.
        correction_detected = bool(user_correction)
        if not correction_detected:
            query = normalized.get("query", "").lower()
            response = normalized.get("response", "").lower()
            if query and response:
                admits_failure = any(m in response for m in [
                    "couldn't find", "could not find", "no flights",
                    "unable to find", "no results", "unfortunately",
                    "wasn't able", "was not able",
                ])
                topic_pivot = False
                pivot_pairs = [
                    ({"flight", "flights"},
                     {"hotel", "hotels", "inn", "lodge", "suites"}),
                    ({"restaurant", "restaurants"},
                     {"cafe", "cafes", "bar", "bars"}),
                    ({"buy", "purchase"}, {"rent", "lease"}),
                ]
                for query_topics, alt_topics in pivot_pairs:
                    if ((query_topics & set(query.split()))
                            and (alt_topics & set(response.split()))):
                        topic_pivot = True
                        break
                correction_detected = admits_failure and topic_pivot

        return {
            "clarification_triggered": clarification,
            "user_correction_detected": correction_detected,
        }

    # ----- Tier 2: Computed features -----

    def _extract_input(self, normalized: dict) -> dict:
        """Estimate input ambiguity (heuristic)."""
        query = normalized.get("query", "")
        score = self._estimate_ambiguity(query)
        return {"ambiguity_score": score}

    def _extract_reasoning(self, normalized: dict) -> dict:
        """Detect replanning and hypothesis branching from LLM step patterns."""
        llm_steps = normalized["llm_steps"]
        replanned = False
        hypothesis_count = 1

        # Replanning markers that indicate a genuine change in approach.
        # "let me try" and "actually" are excluded: they commonly appear
        # before simple retries and do not indicate actual replanning.
        replanning_markers = [
            "different approach", "reconsider", "correction",
            "let me reconsider", "try a different",
            "change strategy", "switch to",
        ]

        if len(llm_steps) >= 2:
            for step in llm_steps[1:]:
                output = step.get("outputs", {}).get("text", "").lower()
                if any(m in output for m in replanning_markers):
                    replanned = True
                    break

        # Detect hypothesis branching
        branching_markers = [
            "alternatively", "on the other hand", "another option",
            "option 1", "option a", "could also mean",
            "there are two", "there are several",
        ]
        for step in llm_steps:
            output = step.get("outputs", {}).get("text", "").lower()
            if output and any(m in output for m in branching_markers):
                hypothesis_count = 2
                break

        return {"replanned": replanned, "hypothesis_count": hypothesis_count}

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
        Higher = better aligned. Includes topic-pivot and negation penalties
        to match callback_handler behavior.
        """
        if not query or not response:
            return 0.5

        query_words = set(query.lower().split())
        response_words = set(response.lower().split())

        if not query_words:
            return 0.5

        # Base: word overlap
        overlap = len(query_words & response_words) / len(query_words)

        # Topic mismatch penalty: response acts on a different entity
        topic_pairs = [
            ({"flight", "flights", "fly", "flying", "airline"},
             {"hotel", "hotels", "inn", "lodge", "suites", "accommodation"}),
            ({"buy", "purchase", "order"},
             {"rent", "lease", "subscribe"}),
            ({"cancel", "cancellation"},
             {"book", "booking", "reserve"}),
        ]
        for query_topics, response_topics in topic_pairs:
            query_has = bool(query_topics & query_words)
            response_has = bool(response_topics & response_words)
            query_addressed = bool(query_topics & response_words)
            if query_has and response_has and not query_addressed:
                overlap *= 0.2

        # Negation penalty
        negation_markers = [
            "couldn't find", "no flights", "unfortunately",
            "unable to find", "no results", "not available",
        ]
        if any(m in response.lower() for m in negation_markers):
            overlap *= 0.5

        return round(min(1.0, overlap), 2)

    # ---- State and grounding (parity with callback_handler) ----

    def _extract_state(self, normalized: dict) -> dict:
        """Infer state.progress_made from tool results."""
        tool_steps = normalized["tool_steps"]
        if not tool_steps:
            return {"progress_made": True}

        negative_count = 0
        total_with_output = 0

        for s in tool_steps:
            output = json.dumps(s.get("outputs", {})).lower()
            if not output:
                continue
            total_with_output += 1
            if s.get("error") or any(m in output for m in
                                     self.TOOL_SOFT_ERROR_MARKERS):
                negative_count += 1

        if total_with_output > 0 and negative_count == total_with_output:
            return {"progress_made": False}

        # Check repeated tool calls with all-negative results
        name_counts = Counter(s["name"] for s in tool_steps)
        if name_counts:
            most_called_name, most_called_count = name_counts.most_common(1)[0]
            if most_called_count >= 2:
                outputs = [
                    json.dumps(s.get("outputs", {})).lower()
                    for s in tool_steps if s["name"] == most_called_name
                ]
                all_negative = all(
                    any(m in o for m in self.TOOL_SOFT_ERROR_MARKERS)
                    for o in outputs if o
                )
                if all_negative:
                    return {"progress_made": False}

        return {"progress_made": True}

    def _extract_grounding(self, normalized: dict) -> dict:
        """Assess evidence grounding quality of the response."""
        tool_steps = normalized["tool_steps"]
        response = normalized.get("response", "")

        # Did any tool provide usable data?
        tool_provided_data = False
        source_data_length = 0
        for s in tool_steps:
            if s.get("error"):
                continue
            output_str = json.dumps(s.get("outputs", {}))
            if not any(m in output_str.lower()
                       for m in self.TOOL_SOFT_ERROR_MARKERS):
                tool_provided_data = True
                source_data_length += len(output_str)

        # Did the response acknowledge uncertainty?
        uncertainty_markers = [
            "couldn't find", "could not find",
            "unable to find", "unable to retrieve",
            "no results", "no relevant results",
            "wasn't able", "was not able",
            "based on general", "based on historical",
            "i don't have", "i do not have",
            "may not accurately reflect",
            "data is outdated", "outdated", "not current",
            "approximately", "estimated",
            "rough estimate", "general estimate",
        ]
        uncertainty_acknowledged = any(
            m in response.lower() for m in uncertainty_markers
        ) if response else False

        response_length = len(response)
        if source_data_length > 0:
            expansion_ratio = round(response_length / source_data_length, 2)
        else:
            expansion_ratio = (
                0.0 if response_length == 0 else float("inf")
            )

        return {
            "tool_provided_data": tool_provided_data,
            "uncertainty_acknowledged": uncertainty_acknowledged,
            "response_length": response_length,
            "source_data_length": source_data_length,
            "expansion_ratio": expansion_ratio,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not args:
        print("Usage: python langchain_adapter.py raw_trace.json [--with-metadata]")
        sys.exit(1)

    with open(args[0], encoding="utf-8") as f:
        raw_log = json.load(f)

    adapter = LangChainAdapter()

    if "--with-metadata" in sys.argv:
        result = adapter.build_with_metadata(raw_log)
    else:
        result = adapter.build_matcher_input(raw_log)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()