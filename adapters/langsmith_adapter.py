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

        return {
            "call_count": call_count,
            "repeat_count": repeat_count,
            "unique_tools": len(set(r["name"] for r in tool_runs)) if tool_runs else 0,
            "error_count": error_count,
        }

    def _extract_interaction(self, normalized: dict) -> dict:
        """Extract interaction signals."""
        feedback = normalized.get("feedback", {})
        user_correction = feedback.get("user_correction", "")

        clarification = False
        for run in normalized["llm_runs"]:
            gens = run.get("outputs", {}).get("generations", [])
            for text in self._extract_generation_texts(gens):
                if any(m in text.lower() for m in
                       ["could you clarify", "did you mean", "can you specify",
                        "what do you mean", "which one", "please clarify"]):
                    clarification = True
                    break

        return {
            "clarification_triggered": clarification,
            "user_correction_detected": bool(user_correction),
        }

    # ----- Tier 2: Computed -----

    def _extract_input(self, normalized: dict) -> dict:
        """Estimate input ambiguity."""
        query = normalized.get("query", "")
        return {"ambiguity_score": self._estimate_ambiguity(query)}

    def _extract_reasoning(self, normalized: dict) -> dict:
        """Detect replanning from LLM run patterns."""
        llm_runs = normalized["llm_runs"]
        replanned = False
        if len(llm_runs) >= 2:
            for run in llm_runs[1:]:
                gens = run.get("outputs", {}).get("generations", [])
                for text in self._extract_generation_texts(gens):
                    if any(w in text.lower() for w in
                           ["let me try", "actually", "correction",
                            "different approach", "reconsider"]):
                        replanned = True
                        break
        return {"replanned": replanned}

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
        """Heuristic alignment score."""
        if not query or not response:
            return 0.5
        query_words = set(query.lower().split())
        response_words = set(response.lower().split())
        if not query_words:
            return 0.5
        overlap = len(query_words & response_words) / len(query_words)
        return round(min(1.0, overlap), 2)


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