"""
callback_handler.py

LangChain/LangGraph callback-based auto-adapter.

Instead of manually exporting JSON traces, this handler collects
events in real-time during agent execution and builds matcher input
automatically.

Usage:
  from adapters.callback_handler import AtlasCallbackHandler

  # Option 1: Callback (works with any LangChain/LangGraph Runnable)
  handler = AtlasCallbackHandler(auto_diagnose=True)
  agent.invoke({"input": "..."}, config={"callbacks": [handler]})
  # → diagnosis prints automatically on completion

  # Option 2: watch() wrapper (LangGraphics-style)
  from adapters.callback_handler import watch
  safe_agent = watch(compiled_graph, auto_diagnose=True)
  await safe_agent.ainvoke({"messages": [...]})

Requirements:
  pip install langchain-core  (only for callback integration)
  The core atlas/debugger pipeline still requires only pyyaml.
"""

import json
import os
from collections import Counter
from pathlib import Path
from datetime import datetime

try:
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.outputs import LLMResult
    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False
    # Provide a dummy base class so the module can be imported
    # even without langchain installed (for testing/inspection)
    class BaseCallbackHandler:
        pass

from llm_failure_atlas.adapters.base_adapter import BaseAdapter


class AtlasCallbackHandler(BaseCallbackHandler):
    """
    LangChain callback handler that collects execution events
    and builds Atlas matcher input automatically.

    Collects:
      - LLM calls (inputs, outputs, model info)
      - Tool calls (name, inputs, outputs, errors)
      - Retriever results (documents, scores, cache info)
      - Chain events (start, end, errors)

    On agent completion (or error), builds telemetry and optionally
    runs the full matcher → debugger pipeline.
    """

    def __init__(self, auto_diagnose: bool = False,
                 auto_pipeline: bool = False,
                 pipeline_callback=None,
                 verbose: bool = True):
        """
        Args:
            auto_diagnose: Run matcher on completion and print diagnosed failures.
            auto_pipeline: Run full pipeline (matcher → debugger) on completion.
                Requires pipeline_callback or agent-failure-debugger installed.
            pipeline_callback: Optional callable(diagnosed: list) -> dict.
                If provided, used instead of importing debugger directly.
                This keeps Atlas free of debugger dependencies.
            verbose: Print results to stdout.
        """
        super().__init__()
        self.auto_diagnose = auto_diagnose
        self.auto_pipeline = auto_pipeline
        self._pipeline_callback = pipeline_callback
        self.verbose = verbose

        # Event collection
        self._llm_calls = []
        self._tool_calls = []
        self._retriever_results = []
        self._chain_errors = []
        self._user_input = ""
        self._final_output = ""
        self._start_time = None
        self._end_time = None
        self._chain_depth = 0

    # ---- LLM events ----

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._llm_calls.append({
            "type": "start",
            "model": serialized.get("name", serialized.get("id", ["unknown"])[-1]),
            "prompts": prompts,
            "time": datetime.now().isoformat(),
        })

    def on_llm_end(self, response: "LLMResult", **kwargs):
        text = ""
        if response.generations:
            for gen_list in response.generations:
                for gen in gen_list:
                    text += gen.text if hasattr(gen, "text") else str(gen)

        # Extract token usage if available
        token_usage = {}
        if hasattr(response, "llm_output") and isinstance(response.llm_output, dict):
            token_usage = response.llm_output.get("token_usage", {})

        if self._llm_calls and self._llm_calls[-1]["type"] == "start":
            self._llm_calls[-1]["type"] = "complete"
            self._llm_calls[-1]["output"] = text
            if token_usage:
                self._llm_calls[-1]["token_usage"] = token_usage
        else:
            entry = {
                "type": "complete",
                "output": text,
                "time": datetime.now().isoformat(),
            }
            if token_usage:
                entry["token_usage"] = token_usage
            self._llm_calls.append(entry)

    def on_llm_error(self, error, **kwargs):
        self._llm_calls.append({
            "type": "error",
            "error": str(error),
            "time": datetime.now().isoformat(),
        })

    # ---- Tool events ----

    def on_tool_start(self, serialized, input_str, **kwargs):
        self._tool_calls.append({
            "name": serialized.get("name", "unknown"),
            "input": input_str,
            "output": None,
            "error": None,
            "time": datetime.now().isoformat(),
        })

    def on_tool_end(self, output, **kwargs):
        if self._tool_calls and self._tool_calls[-1]["output"] is None:
            self._tool_calls[-1]["output"] = str(output)

    def on_tool_error(self, error, **kwargs):
        if self._tool_calls:
            self._tool_calls[-1]["error"] = str(error)

    # ---- Retriever events ----

    def on_retriever_start(self, serialized, query, **kwargs):
        self._retriever_results.append({
            "query": query,
            "documents": [],
            "time": datetime.now().isoformat(),
        })

    def on_retriever_end(self, documents, **kwargs):
        docs = []
        for doc in documents:
            content = doc.page_content if hasattr(doc, "page_content") else str(doc)
            metadata = doc.metadata if hasattr(doc, "metadata") else {}
            docs.append({"content": content, "metadata": metadata})

        if self._retriever_results:
            self._retriever_results[-1]["documents"] = docs

    # ---- Chain events ----

    def on_chain_start(self, serialized, inputs, **kwargs):
        self._chain_depth += 1
        if self._chain_depth == 1:
            self._start_time = datetime.now()
            # Capture user input
            if isinstance(inputs, dict):
                # LangGraph: extract from messages list
                messages = inputs.get("messages", [])
                if messages:
                    for msg in (messages if isinstance(messages, list) else [messages]):
                        # HumanMessage object
                        if hasattr(msg, "content") and hasattr(msg, "type"):
                            if msg.type == "human":
                                self._user_input = msg.content
                                break
                        # Dict format
                        elif isinstance(msg, dict):
                            if msg.get("role") == "user" or "Human" in str(msg.get("id", "")):
                                self._user_input = msg.get("content", "")
                                break
                # Fallback: standard input/query keys
                if not self._user_input:
                    self._user_input = (
                        inputs.get("input", "")
                        or inputs.get("query", "")
                    )
            elif isinstance(inputs, str):
                self._user_input = inputs

    def on_chain_end(self, outputs, **kwargs):
        self._chain_depth -= 1
        self._end_time = datetime.now()

        # Always capture output (last one wins)
        if isinstance(outputs, dict):
            self._final_output = (
                outputs.get("output", "")
                or outputs.get("response", "")
                or str(next(iter(outputs.values()), ""))
            )
        elif isinstance(outputs, str):
            self._final_output = outputs

        # Auto-diagnose only on outermost chain completion
        if self._chain_depth <= 0 and (self.auto_diagnose or self.auto_pipeline):
            self._run_diagnosis()

    def on_chain_error(self, error, **kwargs):
        self._chain_depth -= 1
        self._chain_errors.append(str(error))
        self._end_time = datetime.now()

        if self._chain_depth <= 0 and (self.auto_diagnose or self.auto_pipeline):
            self._run_diagnosis()

    # ---- Telemetry building ----

    def build_telemetry(self) -> dict:
        """
        Build matcher-compatible telemetry from collected events.
        Same output format as langchain_adapter / langsmith_adapter.
        """
        return {
            "input": self._build_input(),
            "interaction": self._build_interaction(),
            "reasoning": self._build_reasoning(),
            "cache": self._build_cache(),
            "retrieval": self._build_retrieval(),
            "response": self._build_response(),
            "tools": self._build_tools(),
            "state": self._build_state(),
            "grounding": self._build_grounding(),
            "context": self._build_context(),
        }

    def _build_input(self) -> dict:
        query = self._user_input
        score = 0.3
        words = query.split() if query else []
        if len(words) <= 3:
            score += 0.2
        elif len(words) <= 6:
            score += 0.1
        ambiguous = {"it", "that", "this", "they", "them"}
        if any(w.lower() in ambiguous for w in words):
            score += 0.15
        # Multiple possible intents
        if any(w.lower() in {"or", "maybe", "either", "perhaps"} for w in words):
            score += 0.15
        return {"ambiguity_score": min(1.0, round(score, 2))}

    def _build_interaction(self) -> dict:
        clarification = False
        # Markers that indicate the agent is requesting clarification.
        # Two groups: direct clarification questions and information
        # requests that implicitly seek clarification (common in Claude).
        CLARIFICATION_MARKERS = [
            # Direct clarification
            "could you clarify", "did you mean", "can you specify",
            "what do you mean", "please clarify", "which one",
            # Information requests (Claude-style clarification)
            "could you provide", "could you please provide",
            "can you provide", "i need the", "i need to know",
            "what is the", "what is your", "which ",
            "please provide", "please specify",
        ]
        for call in self._llm_calls:
            output = call.get("output", "")
            if output and any(m in output.lower() for m in
                              CLARIFICATION_MARKERS):
                clarification = True
                break

        # Infer user_correction_detected in callback mode:
        # If the response explicitly admits failure on the original task
        # AND pivots to a different topic, this is structurally equivalent
        # to "the user would need to correct this."
        correction_inferred = False
        if self._final_output and self._user_input:
            response = self._final_output.lower()
            query = self._user_input.lower()

            # Response admits failure
            admits_failure = any(m in response for m in [
                "couldn't find", "could not find", "no flights",
                "unable to find", "no results", "unfortunately",
                "wasn't able", "was not able",
            ])

            # Response pivots to different topic than requested
            topic_pivot = False
            pivot_pairs = [
                ({"flight", "flights"}, {"hotel", "hotels", "inn", "lodge", "suites"}),
                ({"restaurant", "restaurants"}, {"cafe", "cafes", "bar", "bars"}),
                ({"buy", "purchase"}, {"rent", "lease"}),
            ]
            for query_topics, alt_topics in pivot_pairs:
                if (query_topics & set(query.split())) and (alt_topics & set(response.split())):
                    topic_pivot = True
                    break

            correction_inferred = admits_failure and topic_pivot

        return {
            "clarification_triggered": clarification,
            "user_correction_detected": correction_inferred,
        }

    def _build_reasoning(self) -> dict:
        llm_complete = [c for c in self._llm_calls if c.get("type") == "complete"]
        replanned = False
        hypothesis_count = 1  # Default: single interpretation

        # Replanning markers that indicate a genuine change in approach.
        # "let me try" is excluded: it commonly appears before simple
        # retries (especially with Claude) and does not indicate actual
        # replanning. "actually" is also excluded because it frequently
        # precedes minor corrections rather than strategy changes.
        # Retained markers require explicit mention of a new strategy.
        REPLANNING_MARKERS = [
            "different approach", "reconsider", "correction",
            "let me reconsider", "try a different",
            "change strategy", "switch to",
        ]

        if len(llm_complete) >= 2:
            for call in llm_complete[1:]:
                output = call.get("output", "").lower()
                if any(m in output for m in REPLANNING_MARKERS):
                    replanned = True
                    break

        # Detect hypothesis branching in LLM outputs
        for call in llm_complete:
            output = call.get("output", "")
            if output and any(m in output.lower() for m in
                              ["alternatively", "on the other hand", "another option",
                               "option 1", "option a", "could also mean",
                               "there are two", "there are several"]):
                hypothesis_count = 2
                break

        return {"replanned": replanned, "hypothesis_count": hypothesis_count}

    def _build_cache(self) -> dict:
        # Cache info must come from retriever metadata
        for ret in self._retriever_results:
            for doc in ret.get("documents", []):
                meta = doc.get("metadata", {})
                if "cache_hit" in meta:
                    return {
                        "hit": bool(meta.get("cache_hit")),
                        "similarity": float(meta.get("cache_similarity", 0.0)),
                        "query_intent_similarity": self._compute_intent_sim(),
                    }
        return {"hit": False, "similarity": 0.0, "query_intent_similarity": 1.0}

    # Adversarial patterns in retrieved content that may override
    # system or task instructions (prompt injection via retrieval).
    ADVERSARIAL_PATTERNS = [
        "ignore previous instructions",
        "ignore all previous",
        "ignore the above",
        "disregard previous",
        "disregard all previous",
        "forget your instructions",
        "new instructions:",
        "instead, do the following",
        "override:",
        "system prompt:",
        "you are now",
        "act as",
        "do not follow",
        "stop being",
    ]

    def _build_retrieval(self) -> dict:
        """Build retrieval telemetry from retriever events.

        When retriever events are present, scans retrieved documents for
        adversarial injection patterns and computes coverage heuristic.
        These fields feed prompt_injection_via_retrieval and
        context_truncation_loss patterns.
        """
        if not self._retriever_results:
            return {"skipped": True}

        # Scan all retrieved documents for adversarial patterns
        contains_instruction = False
        adversarial_matches = 0
        total_docs = 0

        for ret in self._retriever_results:
            for doc in ret.get("documents", []):
                total_docs += 1
                content = doc.get("content", "").lower()
                for pattern in self.ADVERSARIAL_PATTERNS:
                    if pattern in content:
                        contains_instruction = True
                        adversarial_matches += 1
                        break  # one match per doc is enough

        adversarial_score = (
            adversarial_matches / total_docs if total_docs > 0 else 0.0
        )

        # override_detected: adversarial content found AND the response
        # diverges from what we'd expect.  Full compliance check requires
        # instruction_priority_inversion (not yet observable), so we use
        # a conservative proxy: adversarial content exists.
        override_detected = contains_instruction

        # Expected coverage heuristic: fraction of retriever queries
        # that returned at least one document.
        queries_with_results = sum(
            1 for ret in self._retriever_results
            if ret.get("documents")
        )
        expected_coverage = (
            queries_with_results / len(self._retriever_results)
            if self._retriever_results else 0.0
        )

        return {
            "skipped": False,
            "contains_instruction": contains_instruction,
            "override_detected": override_detected,
            "adversarial_score": round(adversarial_score, 2),
            "expected_coverage": round(expected_coverage, 2),
        }

    def _build_response(self) -> dict:
        return {"alignment_score": self._compute_alignment()}

    def _build_grounding(self) -> dict:
        """Assess evidence grounding quality of the final response.

        This section captures whether the agent had sufficient data to
        support its answer.  It does NOT trigger any failure pattern
        today — it enriches telemetry for future Sufficiency analysis.

        Fields:
          tool_provided_data: True if at least one tool returned usable
              (non-error, non-empty) output.
          uncertainty_acknowledged: True if the final response contains
              explicit language indicating the answer may lack grounding.
          response_length: character count of final output.
          source_data_length: total character count of usable tool outputs.
          expansion_ratio: response_length / source_data_length.
              High ratio (>5) with uncertainty_acknowledged=False
              indicates the LLM supplemented thin evidence without
              disclosure ("thin grounding" pattern).
        """
        # Did any tool provide usable data?
        tool_provided_data = False
        source_data_length = 0
        for c in self._tool_calls:
            if c.get("error"):
                continue
            output_str = str(c.get("output", ""))
            if not any(m in output_str.lower() for m in self.TOOL_SOFT_ERROR_MARKERS):
                tool_provided_data = True
                source_data_length += len(output_str)

        # Did the final response acknowledge uncertainty?
        uncertainty_acknowledged = False
        if self._final_output:
            response = self._final_output.lower()
            uncertainty_markers = [
                # Data absence
                "couldn't find", "could not find",
                "unable to find", "unable to retrieve",
                "unable to fetch",
                "no results", "no relevant results",
                "did not yield", "did not return",
                "wasn't able", "was not able",
                "service unavailable", "service issue",
                "don't have access", "do not have access",
                "can't get", "cannot get",
                # Grounding qualification
                "based on general", "based on historical",
                "based on typical", "based on common",
                "up to my last training",
                "as of my last knowledge",
                "i don't have", "i do not have",
                # Data staleness (discovered in grounding precision tests)
                "may not accurately reflect",
                "data is outdated", "data is from",
                "outdated", "not current",
                "may have changed", "may have shifted",
                "recommend seeking more recent",
                "recommend checking the latest",
                "for the most accurate",
                # Estimation disclosure
                "approximately", "estimated",
                "rough estimate", "general estimate",
                "as of the latest available",
            ]
            uncertainty_acknowledged = any(m in response for m in uncertainty_markers)

        response_length = len(self._final_output) if self._final_output else 0

        # Expansion ratio: how much the response expands beyond source data
        if source_data_length > 0:
            expansion_ratio = round(response_length / source_data_length, 2)
        else:
            expansion_ratio = 0.0 if response_length == 0 else float("inf")

        return {
            "tool_provided_data": tool_provided_data,
            "uncertainty_acknowledged": uncertainty_acknowledged,
            "response_length": response_length,
            "source_data_length": source_data_length,
            "expansion_ratio": expansion_ratio,
        }

    # Known model context window sizes (tokens).
    # Used for truncation heuristic — conservative estimates.
    MODEL_CONTEXT_LIMITS = {
        "gpt-4o-mini": 128000,
        "gpt-4o": 128000,
        "gpt-4": 8192,
        "gpt-4-turbo": 128000,
        "gpt-3.5-turbo": 16385,
        # Anthropic models
        "claude-haiku-4-5": 200000,
        "claude-sonnet-4": 200000,
        "claude-opus-4": 200000,
        "claude-3-5-haiku": 200000,
        "claude-3-5-sonnet": 200000,
    }
    # Fraction of context window used → consider truncation risk
    TRUNCATION_THRESHOLD = 0.85

    def _build_context(self) -> dict:
        """Estimate context truncation risk from token usage.

        Uses token_usage from LLM response metadata (when available)
        to detect if input tokens approach the model's context window.
        This is a heuristic — actual truncation happens inside the
        LLM and is not directly observable from callbacks.

        Fields:
          truncated: True if input tokens exceed TRUNCATION_THRESHOLD
              of the model's context window.
          critical_info_present: Always False (requires domain knowledge,
              not deterministically inferable from callback data).
          max_input_tokens: highest input token count observed.
          context_utilization: max_input_tokens / model_limit.
        """
        max_input_tokens = 0
        model_name = ""

        for call in self._llm_calls:
            usage = call.get("token_usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            if input_tokens > max_input_tokens:
                max_input_tokens = input_tokens
            if not model_name:
                model_name = call.get("model", "")

        # Find context limit for this model
        context_limit = None
        model_lower = model_name.lower()
        for name, limit in self.MODEL_CONTEXT_LIMITS.items():
            if name in model_lower:
                context_limit = limit
                break

        if context_limit and max_input_tokens > 0:
            utilization = max_input_tokens / context_limit
            truncated = utilization >= self.TRUNCATION_THRESHOLD
        else:
            utilization = 0.0
            truncated = False

        return {
            "truncated": truncated,
            "critical_info_present": False,  # not inferable
            "max_input_tokens": max_input_tokens,
            "context_utilization": round(utilization, 4),
        }

    # Markers that indicate a tool returned an error or empty result
    # in its output text (not via on_tool_error callback).
    TOOL_SOFT_ERROR_MARKERS = [
        "error", "unavailable", "service unavailable",
        "could not", "failed to", "exception",
        "no results", "0 results", "0 matching",
        "not found", "no data", "no records",
        "empty", "none found", "[]",
    ]

    def _build_tools(self) -> dict:
        # Count by tool name only — LLM may vary params in a loop
        name_counts = Counter(c["name"] for c in self._tool_calls)
        max_repeat = max(name_counts.values()) if name_counts else 0
        repeat_count = max_repeat - 1 if max_repeat > 1 else 0

        # Hard errors: raised via on_tool_error callback.
        # These represent tool-level failures (HTTP 4xx/5xx, timeouts,
        # MCP connectivity, exceptions) — not soft empty results.
        error_count = sum(1 for c in self._tool_calls if c.get("error"))

        # Soft errors: tool returned successfully but output text
        # contains error/empty markers. These are invisible to
        # on_tool_error but indicate the tool provided no useful data.
        soft_error_count = 0
        for c in self._tool_calls:
            if c.get("error"):
                continue  # already counted as hard error
            output = str(c.get("output", "")).lower()
            if any(m in output for m in self.TOOL_SOFT_ERROR_MARKERS):
                soft_error_count += 1

        return {
            "call_count": len(self._tool_calls),
            "repeat_count": repeat_count,
            "unique_tools": len(name_counts) if name_counts else 0,
            "error_count": error_count,
            "soft_error_count": soft_error_count,
            "hard_error_detected": error_count > 0,
        }

    # Minimum repeat count to consider a tool as looping.
    LOOP_THRESHOLD = 3

    def _build_state(self) -> dict:
        """Infer execution state from tool results.

        Produces:
          progress_made: bool — backward compatible, True if any tool
              produced usable output.
          tool_progress: dict — per-tool breakdown of calls, successes,
              failures, and progress status.
          any_tool_looping: bool — True if any single tool was called
              LOOP_THRESHOLD+ times with zero successes.
        """
        if not self._tool_calls:
            return {
                "progress_made": True,
                "tool_progress": {},
                "any_tool_looping": False,
                "output_produced": bool(self._final_output and
                                        len(self._final_output.strip()) > 0),
                "chain_error_occurred": len(self._chain_errors) > 0,
            }

        negative_markers = self.TOOL_SOFT_ERROR_MARKERS

        # Build per-tool progress
        tool_progress = {}
        for c in self._tool_calls:
            name = c.get("name", "unknown")
            if name not in tool_progress:
                tool_progress[name] = {
                    "calls": 0, "successes": 0, "failures": 0,
                    "progress": False,
                }
            entry = tool_progress[name]
            entry["calls"] += 1

            output = str(c.get("output", "")).lower()
            is_failure = bool(c.get("error")) or (
                bool(output)
                and any(m in output for m in negative_markers)
            )

            if is_failure:
                entry["failures"] += 1
            elif output:
                entry["successes"] += 1
                entry["progress"] = True

        # any_tool_looping: any tool called LOOP_THRESHOLD+ times
        # with zero successes
        any_tool_looping = any(
            tp["calls"] >= self.LOOP_THRESHOLD and tp["successes"] == 0
            for tp in tool_progress.values()
        )

        # progress_made: backward compatible — True if any tool made progress
        progress_made = any(
            tp["progress"] for tp in tool_progress.values()
        )

        return {
            "progress_made": progress_made,
            "tool_progress": tool_progress,
            "any_tool_looping": any_tool_looping,
            "output_produced": bool(self._final_output and
                                    len(self._final_output.strip()) > 0),
            "chain_error_occurred": len(self._chain_errors) > 0,
        }

    def _compute_intent_sim(self) -> float:
        query = self._user_input.lower()
        if not query:
            return 1.0
        query_words = set(query.split())
        if not query_words:
            return 1.0
        best = 0.0
        for ret in self._retriever_results:
            for doc in ret.get("documents", []):
                content = doc.get("content", "").lower()
                doc_words = set(content.split())
                if doc_words:
                    overlap = len(query_words & doc_words) / len(query_words)
                    best = max(best, overlap)
        return round(best, 2)

    def _compute_alignment(self) -> float:
        if not self._user_input or not self._final_output:
            return 0.5
        query = self._user_input.lower()
        response = self._final_output.lower()
        query_words = set(query.split())
        response_words = set(response.split())
        if not query_words:
            return 0.5

        # Base: word overlap
        overlap = len(query_words & response_words) / len(query_words)

        # Topic mismatch penalty: detect when response acts on different entity
        # e.g., asked about flights → answered about hotels
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
                # Asked about X, got Y instead, X not addressed
                overlap *= 0.2  # Heavy penalty

        # Negation penalty: "couldn't find", "no results", "unfortunately"
        negation_markers = ["couldn't find", "no flights", "unfortunately",
                            "unable to find", "no results", "not available"]
        if any(m in response for m in negation_markers):
            overlap *= 0.5

        return round(min(1.0, overlap), 2)

    # ---- Diagnosis ----

    # Meta failure patterns (run in second pass after domain patterns)
    META_PATTERNS = {"unmodeled_failure", "insufficient_observability", "conflicting_signals"}

    # Expected telemetry fields for observability checking
    EXPECTED_FIELDS = [
        "input.ambiguity_score",
        "interaction.clarification_triggered",
        "interaction.user_correction_detected",
        "reasoning.replanned",
        "cache.hit",
        "cache.similarity",
        "cache.query_intent_similarity",
        "retrieval.skipped",
        "response.alignment_score",
    ]

    def _count_missing_fields(self, telemetry: dict) -> tuple:
        """Count how many expected fields are missing from telemetry."""
        missing = 0
        total = len(self.EXPECTED_FIELDS)
        for field_path in self.EXPECTED_FIELDS:
            parts = field_path.split(".")
            obj = telemetry
            found = True
            for p in parts:
                if isinstance(obj, dict) and p in obj:
                    obj = obj[p]
                else:
                    found = False
                    break
            if not found:
                missing += 1
        return missing, total

    def _run_diagnosis(self):
        """Run matcher (and optionally full pipeline) on collected telemetry.

        Two-pass approach:
          Pass 1: Run domain patterns (non-meta)
          Pass 2: Inject meta.* fields, run meta patterns
        """
        telemetry = self.build_telemetry()
        self.last_telemetry = telemetry

        try:
            from llm_failure_atlas.matcher import run as run_matcher
            from llm_failure_atlas.resource_loader import get_patterns_dir
            import tempfile

            tmp_path = os.path.join(tempfile.gettempdir(), "atlas_callback_telemetry.json")
            failures_dir = Path(get_patterns_dir())

            # Pass 1: domain patterns
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(telemetry, f)

            domain_diagnosed = []
            for pf in sorted(failures_dir.glob("*.yaml")):
                if pf.stem in self.META_PATTERNS:
                    continue
                result = run_matcher(str(pf), tmp_path)
                if result.get("diagnosed"):
                    domain_diagnosed.append(result)

            # Pass 2: inject meta fields, run meta patterns
            missing_count, total_fields = self._count_missing_fields(telemetry)
            telemetry["meta"] = {
                "diagnosed_failure_count": len(domain_diagnosed),
                "missing_field_count": missing_count,
                "total_expected_fields": total_fields,
            }

            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(telemetry, f)

            meta_diagnosed = []
            for pf in sorted(failures_dir.glob("*.yaml")):
                if pf.stem not in self.META_PATTERNS:
                    continue
                result = run_matcher(str(pf), tmp_path)
                if result.get("diagnosed"):
                    meta_diagnosed.append(result)

            diagnosed = domain_diagnosed + meta_diagnosed
            self.last_diagnosed = diagnosed

            if self.verbose:
                print(f"\n{'='*50}")
                print(f"  Atlas Auto-Diagnosis ({len(diagnosed)} failures detected)")
                print(f"{'='*50}")
                for d in diagnosed:
                    tag = " [meta]" if d["failure_id"] in self.META_PATTERNS else ""
                    print(f"  ✅ {d['failure_id']:40s} conf={d['confidence']}{tag}")
                if not diagnosed:
                    print("  No failures detected.")

            # Full pipeline if requested
            if self.auto_pipeline and diagnosed:
                self._run_pipeline(diagnosed)

        except ImportError as e:
            if self.verbose:
                print(f"\n⚠ Atlas auto-diagnosis unavailable: {e}")

    def _run_pipeline(self, diagnosed: list):
        """Run full debugger pipeline on diagnosed failures.

        Uses pipeline_callback if provided, otherwise attempts to import
        agent-failure-debugger. Atlas itself has no hard dependency on debugger.
        """
        callback = self._pipeline_callback
        if callback is None:
            try:
                from agent_failure_debugger.pipeline import run_pipeline
                callback = lambda d: run_pipeline(
                    d, use_learning=True, top_k=1, include_explanation=True,
                )
            except ImportError:
                if self.verbose:
                    print("\n⚠ Debugger pipeline unavailable: "
                          "install agent-failure-debugger or provide pipeline_callback")
                return

        try:
            result = callback(diagnosed)
            self.last_pipeline_result = result
            s = result.get("summary", {})

            if self.verbose:
                print(f"\n  Root cause:  {s.get('root_cause', '-')} (conf={s.get('root_confidence', '-')})")
                print(f"  Failures:    {s.get('failure_count', '-')}")
                print(f"  Fixes:       {s.get('fix_count', '-')}")
                print(f"  Gate:        {s.get('gate_mode', '-')} (score={s.get('gate_score', '-')})")

                expl = result.get("explanation")
                if expl:
                    print(f"\n  Explanation:")
                    if expl.get("context_summary"):
                        print(f"    Context: {expl['context_summary']}")
                    if expl.get("interpretation"):
                        print(f"    Interpretation: {expl['interpretation']}")
                    risk = expl.get("risk", {})
                    if risk:
                        print(f"    Risk: {risk.get('level', '-').upper()}")
                    if expl.get("recommendation"):
                        print(f"    Action: {expl['recommendation']}")

        except Exception as e:
            if self.verbose:
                print(f"\n⚠ Pipeline execution failed: {e}")

    # ---- Data access ----

    def get_events(self) -> dict:
        """Return raw collected events for inspection."""
        return {
            "llm_calls": self._llm_calls,
            "tool_calls": self._tool_calls,
            "retriever_results": self._retriever_results,
            "chain_errors": self._chain_errors,
            "user_input": self._user_input,
            "final_output": self._final_output,
        }

    def reset(self):
        """Clear all collected events for reuse."""
        self._llm_calls.clear()
        self._tool_calls.clear()
        self._retriever_results.clear()
        self._chain_errors.clear()
        self._user_input = ""
        self._final_output = ""
        self._start_time = None
        self._end_time = None
        self._chain_depth = 0
# ---------------------------------------------------------------------------
# watch() wrapper (LangGraphics-style)
# ---------------------------------------------------------------------------

def watch(compiled_graph, auto_diagnose: bool = True,
          auto_pipeline: bool = False, verbose: bool = True):
    """
    LangGraphics-style wrapper: wraps a compiled LangGraph graph
    with Atlas diagnosis.

    Usage:
        from adapters.callback_handler import watch
        graph = watch(workflow.compile(), auto_diagnose=True)
        await graph.ainvoke({"messages": [...]})

    The original graph behavior is completely unchanged.
    Atlas diagnosis runs after each invocation.
    """
    if not HAS_LANGCHAIN:
        raise ImportError(
            "langchain-core is required for watch(). "
            "Install with: pip install langchain-core"
        )

    handler = AtlasCallbackHandler(
        auto_diagnose=auto_diagnose,
        auto_pipeline=auto_pipeline,
        verbose=verbose,
    )

    class WatchedGraph:
        """Transparent wrapper that injects Atlas callback."""

        def __init__(self, graph, handler):
            self._graph = graph
            self._handler = handler

        def invoke(self, inputs, config=None, **kwargs):
            config = self._inject_callback(config)
            self._handler.reset()
            return self._graph.invoke(inputs, config=config, **kwargs)

        async def ainvoke(self, inputs, config=None, **kwargs):
            config = self._inject_callback(config)
            self._handler.reset()
            return await self._graph.ainvoke(inputs, config=config, **kwargs)

        def stream(self, inputs, config=None, **kwargs):
            config = self._inject_callback(config)
            self._handler.reset()
            return self._graph.stream(inputs, config=config, **kwargs)

        async def astream(self, inputs, config=None, **kwargs):
            config = self._inject_callback(config)
            self._handler.reset()
            return await self._graph.astream(inputs, config=config, **kwargs)

        def _inject_callback(self, config):
            config = config or {}
            callbacks = config.get("callbacks", [])
            if self._handler not in callbacks:
                callbacks = list(callbacks) + [self._handler]
            config["callbacks"] = callbacks
            return config

        # Delegate everything else to the original graph
        def __getattr__(self, name):
            return getattr(self._graph, name)

    return WatchedGraph(compiled_graph, handler)