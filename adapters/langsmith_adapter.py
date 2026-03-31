"""
langsmith_adapter.py

Phase 24: LangSmith run-tree trace → matcher input adapter.

Converts LangSmith exported traces (run-tree format) into the
telemetry format that matcher.py expects.

LangSmith traces use a hierarchical run-tree structure:
  - Root run (type: "chain") contains child_runs
  - Child runs have run_type: "llm" | "retriever" | "tool" | "chain"
  - Each run has: id, name, inputs, outputs, extra, error, child_runs

Usage:
  python adapters/langsmith_adapter.py trace.json > matcher_input.json

  from adapters.langsmith_adapter import LangSmithAdapter
  adapter = LangSmithAdapter()
  matcher_input = adapter.build_matcher_input(trace)
"""

import json
import sys
from collections import Counter

try:
    from base_adapter import BaseAdapter
except ImportError:
    from adapters.base_adapter import BaseAdapter


class LangSmithAdapter(BaseAdapter):
    """
    Adapter for LangSmith run-tree trace format.

    Expected input: a run-tree dict with:
      - inputs.messages (user messages)
      - outputs.messages (agent messages)
      - child_runs[] (nested execution steps)
      - feedback_stats (optional)
      - extra.metadata (optional)
    """

    source = "langsmith"

    def normalize(self, raw_log: dict) -> dict:
        """Flatten run-tree into categorized step lists."""
        child_runs = raw_log.get("child_runs", [])

        # Recursively collect all runs by type
        all_runs = self._collect_runs(child_runs)

        # Extract user query from inputs
        query = self._extract_query(raw_log)

        # Extract agent response from outputs
        response = self._extract_response(raw_log)

        # Extract feedback
        feedback = self._extract_feedback(raw_log)

        return {
            "query": query,
            "response": response,
            "llm_runs": all_runs.get("llm", []),
            "retriever_runs": all_runs.get("retriever", []),
            "tool_runs": all_runs.get("tool", []),
            "feedback": feedback,
            "raw": raw_log,
        }

    def _collect_runs(self, runs: list, depth: int = 0) -> dict:
        """Recursively collect all runs by type from the tree."""
        by_type = {"llm": [], "retriever": [], "tool": [], "chain": []}
        for run in runs:
            rt = run.get("run_type", "chain")
            if rt in by_type:
                by_type[rt].append(run)
            # Recurse into children
            children = run.get("child_runs", [])
            if children:
                child_by_type = self._collect_runs(children, depth + 1)
                for t, items in child_by_type.items():
                    by_type.setdefault(t, []).extend(items)
        return by_type

    def _extract_query(self, raw_log: dict) -> str:
        """Extract user query from LangSmith inputs."""
        inputs = raw_log.get("inputs", {})
        messages = inputs.get("messages", [])
        for msg in messages:
            ids = msg.get("id", [])
            if any("Human" in str(i) for i in ids):
                return msg.get("content", "")
        # Fallback: first message content
        if messages:
            return messages[0].get("content", "")
        return inputs.get("input", inputs.get("query", ""))

    def _extract_response(self, raw_log: dict) -> str:
        """Extract agent response from LangSmith outputs."""
        outputs = raw_log.get("outputs", {})
        messages = outputs.get("messages", [])
        for msg in messages:
            ids = msg.get("id", [])
            if any("AI" in str(i) for i in ids):
                return msg.get("content", "")
        if messages:
            return messages[-1].get("content", "")
        return outputs.get("output", "")

    def _extract_feedback(self, raw_log: dict) -> dict:
        """Extract user feedback from LangSmith trace."""
        feedback = {}

        # From feedback_stats
        stats = raw_log.get("feedback_stats", {})
        if stats:
            score = stats.get("user_score", {})
            if score.get("avg", 5) <= 2:
                feedback["low_score"] = True

        # From extra.metadata
        meta = raw_log.get("extra", {}).get("metadata", {})
        user_fb = meta.get("user_feedback", "")
        if user_fb:
            feedback["user_correction"] = user_fb

        return feedback

    def extract_features(self, normalized: dict) -> dict:
        """Build matcher-compatible telemetry from normalized trace."""
        return {
            "input": self._extract_input(normalized),
            "interaction": self._extract_interaction(normalized),
            "reasoning": self._extract_reasoning(normalized),
            "cache": self._extract_cache(normalized),
            "retrieval": self._extract_retrieval(normalized),
            "response": self._extract_response_features(normalized),
            "tools": self._extract_tools(normalized),
            "state": self._extract_state(normalized),
            "grounding": self._extract_grounding(normalized),
        }

    # ----- Tier 1: Deterministic -----

    @staticmethod
    def _extract_generation_texts(gens) -> list:
        """Extract text from generations in any format.

        Handles:
          - Sample format: [{"text": "..."}]
          - Real LangSmith: [[{"message": {"kwargs": {"content": "..."}}}]]
          - Mixed formats
        """
        texts = []
        for gen in gens:
            if isinstance(gen, list):
                # Nested list: [[{...}]]
                for inner in gen:
                    if isinstance(inner, dict):
                        # Try message.kwargs.content first (real LangSmith)
                        msg = inner.get("message", {})
                        if isinstance(msg, dict):
                            kwargs = msg.get("kwargs", {})
                            if isinstance(kwargs, dict):
                                content = kwargs.get("content", "")
                                if content:
                                    texts.append(content)
                                    continue
                        # Fallback to .text
                        text = inner.get("text", "")
                        if text:
                            texts.append(text)
            elif isinstance(gen, dict):
                # Flat dict: {"text": "..."}
                msg = gen.get("message", {})
                if isinstance(msg, dict):
                    kwargs = msg.get("kwargs", {})
                    if isinstance(kwargs, dict):
                        content = kwargs.get("content", "")
                        if content:
                            texts.append(content)
                            continue
                text = gen.get("text", "")
                if text:
                    texts.append(text)
        return texts

    def _extract_cache(self, normalized: dict) -> dict:
        """Extract cache info from retriever runs."""
        for run in normalized["retriever_runs"]:
            meta = run.get("extra", {}).get("metadata", {})
            if meta.get("cache_hit") is not None:
                return {
                    "hit": bool(meta.get("cache_hit", False)),
                    "similarity": float(meta.get("cache_similarity", 0.0)),
                    "query_intent_similarity": self._compute_intent_similarity(normalized),
                }
        return {"hit": False, "similarity": 0.0, "query_intent_similarity": 1.0}

    def _extract_retrieval(self, normalized: dict) -> dict:
        """Extract retrieval state."""
        for run in normalized["retriever_runs"]:
            meta = run.get("extra", {}).get("metadata", {})
            return {"skipped": bool(meta.get("retrieval_skipped", False))}
        if not normalized["retriever_runs"]:
            return {"skipped": True}
        return {"skipped": False}

    TOOL_SOFT_ERROR_MARKERS = [
        "error", "unavailable", "service unavailable",
        "could not", "failed to", "exception",
        "no results", "0 results", "0 matching",
        "not found", "no data", "no records",
        "empty", "none found", "[]",
    ]

    def _extract_tools(self, normalized: dict) -> dict:
        """Extract tool call patterns."""
        tool_runs = normalized["tool_runs"]
        call_count = len(tool_runs)

        calls = [(r["name"], json.dumps(r.get("inputs", {}), sort_keys=True))
                 for r in tool_runs]
        call_counts = Counter(calls)
        max_repeat = max(call_counts.values()) if call_counts else 0
        repeat_count = max_repeat - 1 if max_repeat > 1 else 0

        error_count = sum(1 for r in tool_runs if r.get("error"))

        soft_error_count = 0
        for r in tool_runs:
            if r.get("error"):
                continue
            output = json.dumps(r.get("outputs", {})).lower()
            if any(m in output for m in self.TOOL_SOFT_ERROR_MARKERS):
                soft_error_count += 1

        return {
            "call_count": call_count,
            "repeat_count": repeat_count,
            "unique_tools": len(set(r["name"] for r in tool_runs)) if tool_runs else 0,
            "error_count": error_count,
            "soft_error_count": soft_error_count,
        }

    def _extract_interaction(self, normalized: dict) -> dict:
        """Extract interaction signals."""
        feedback = normalized.get("feedback", {})
        user_correction = feedback.get("user_correction", "")

        clarification_markers = [
            "could you clarify", "did you mean", "can you specify",
            "what do you mean", "please clarify", "which one",
            "could you provide", "could you please provide",
            "can you provide", "i need the", "i need to know",
            "what is the", "what is your", "which ",
            "please provide", "please specify",
        ]
        clarification = False
        for run in normalized["llm_runs"]:
            gens = run.get("outputs", {}).get("generations", [])
            for text in self._extract_generation_texts(gens):
                if any(m in text.lower() for m in clarification_markers):
                    clarification = True
                    break

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

    # ----- Tier 2: Computed -----

    def _extract_input(self, normalized: dict) -> dict:
        """Estimate input ambiguity."""
        query = normalized.get("query", "")
        return {"ambiguity_score": self._estimate_ambiguity(query)}

    def _extract_reasoning(self, normalized: dict) -> dict:
        """Detect replanning and hypothesis branching from LLM run patterns."""
        llm_runs = normalized["llm_runs"]
        replanned = False
        hypothesis_count = 1

        replanning_markers = [
            "different approach", "reconsider", "correction",
            "let me reconsider", "try a different",
            "change strategy", "switch to",
        ]
        branching_markers = [
            "alternatively", "on the other hand", "another option",
            "option 1", "option a", "could also mean",
            "there are two", "there are several",
        ]

        if len(llm_runs) >= 2:
            for run in llm_runs[1:]:
                gens = run.get("outputs", {}).get("generations", [])
                for text in self._extract_generation_texts(gens):
                    if any(m in text.lower() for m in replanning_markers):
                        replanned = True
                        break

        for run in llm_runs:
            gens = run.get("outputs", {}).get("generations", [])
            for text in self._extract_generation_texts(gens):
                if any(m in text.lower() for m in branching_markers):
                    hypothesis_count = 2
                    break

        return {"replanned": replanned, "hypothesis_count": hypothesis_count}

    def _extract_response_features(self, normalized: dict) -> dict:
        """Estimate response alignment."""
        query = normalized.get("query", "")
        response = normalized.get("response", "")
        return {"alignment_score": self._estimate_alignment(query, response)}

    def _estimate_ambiguity(self, query: str) -> float:
        """Heuristic ambiguity score."""
        if not query:
            return 0.5
        score = 0.3
        words = query.split()
        if len(words) <= 3:
            score += 0.2
        elif len(words) <= 6:
            score += 0.1
        ambiguous = {"it", "that", "this", "they", "them"}
        if any(w.lower() in ambiguous for w in words):
            score += 0.15
        if any(w.lower() in {"or", "maybe", "either", "perhaps"} for w in words):
            score += 0.15
        return min(1.0, round(score, 2))

    def _compute_intent_similarity(self, normalized: dict) -> float:
        """Heuristic intent similarity."""
        query = normalized.get("query", "").lower()
        if not query:
            return 1.0
        query_words = set(query.split())
        if not query_words:
            return 1.0
        best = 0.0
        for run in normalized["retriever_runs"]:
            docs = run.get("outputs", {}).get("documents", [])
            for doc in docs:
                content = doc.get("page_content", doc.get("content", "")).lower()
                doc_words = set(content.split())
                if doc_words:
                    overlap = len(query_words & doc_words) / len(query_words)
                    best = max(best, overlap)
        return round(best, 2)

    def _estimate_alignment(self, query: str, response: str) -> float:
        """Heuristic alignment score with topic-pivot and negation penalties."""
        if not query or not response:
            return 0.5
        query_words = set(query.lower().split())
        response_words = set(response.lower().split())
        if not query_words:
            return 0.5
        overlap = len(query_words & response_words) / len(query_words)

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

        negation_markers = [
            "couldn't find", "no flights", "unfortunately",
            "unable to find", "no results", "not available",
        ]
        if any(m in response.lower() for m in negation_markers):
            overlap *= 0.5

        return round(min(1.0, overlap), 2)

    LOOP_THRESHOLD = 3

    def _extract_state(self, normalized: dict) -> dict:
        """Infer execution state from tool results.

        Produces per-tool progress breakdown and loop detection.
        See callback_handler._build_state for full documentation.
        """
        tool_runs = normalized["tool_runs"]
        if not tool_runs:
            response = normalized.get("response", "")
            return {
                "progress_made": True,
                "tool_progress": {},
                "any_tool_looping": False,
                "output_produced": bool(response and len(response.strip()) > 0),
                "chain_error_occurred": bool(normalized.get("error")),
            }

        negative_markers = self.TOOL_SOFT_ERROR_MARKERS

        tool_progress = {}
        for r in tool_runs:
            name = r.get("name", "unknown")
            if name not in tool_progress:
                tool_progress[name] = {
                    "calls": 0, "successes": 0, "failures": 0,
                    "progress": False,
                }
            entry = tool_progress[name]
            entry["calls"] += 1

            output = json.dumps(r.get("outputs", {})).lower()
            is_failure = bool(r.get("error")) or (
                bool(output)
                and any(m in output for m in negative_markers)
            )

            if is_failure:
                entry["failures"] += 1
            elif output:
                entry["successes"] += 1
                entry["progress"] = True

        any_tool_looping = any(
            tp["calls"] >= self.LOOP_THRESHOLD and tp["successes"] == 0
            for tp in tool_progress.values()
        )

        progress_made = any(
            tp["progress"] for tp in tool_progress.values()
        )

        response = normalized.get("response", "")
        output_produced = bool(response and len(response.strip()) > 0)
        chain_error_occurred = bool(normalized.get("error"))

        return {
            "progress_made": progress_made,
            "tool_progress": tool_progress,
            "any_tool_looping": any_tool_looping,
            "output_produced": output_produced,
            "chain_error_occurred": chain_error_occurred,
        }

    def _extract_grounding(self, normalized: dict) -> dict:
        """Assess evidence grounding quality of the response."""
        tool_runs = normalized["tool_runs"]
        response = normalized.get("response", "")

        tool_provided_data = False
        source_data_length = 0
        for r in tool_runs:
            if r.get("error"):
                continue
            output_str = json.dumps(r.get("outputs", {}))
            if not any(m in output_str.lower()
                       for m in self.TOOL_SOFT_ERROR_MARKERS):
                tool_provided_data = True
                source_data_length += len(output_str)

        uncertainty_markers = [
            "couldn't find", "could not find",
            "unable to find", "unable to retrieve",
            "no results", "no relevant results",
            "wasn't able", "was not able",
            "don't have access", "do not have access",
            "can't get", "cannot get",
            "based on general", "based on historical",
            "based on typical", "based on common",
            "i don't have", "i do not have",
            "may not accurately reflect",
            "data is outdated", "outdated", "not current",
            "approximately", "estimated",
            "rough estimate", "general estimate",
            "as of the latest available",
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
        print("Usage: python langsmith_adapter.py trace.json [--with-metadata]")
        sys.exit(1)

    with open(args[0], encoding="utf-8") as f:
        raw_log = json.load(f)

    adapter = LangSmithAdapter()

    if "--with-metadata" in sys.argv:
        result = adapter.build_with_metadata(raw_log)
    else:
        result = adapter.build_matcher_input(raw_log)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()