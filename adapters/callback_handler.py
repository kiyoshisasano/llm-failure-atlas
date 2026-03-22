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
import sys
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

# Atlas/debugger imports (relative to repo root)
ATLAS_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ATLAS_ROOT))

try:
    from adapters.base_adapter import BaseAdapter
except ImportError:
    pass


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
                 verbose: bool = True):
        """
        Args:
            auto_diagnose: Run matcher on completion and print diagnosed failures.
            auto_pipeline: Run full pipeline (matcher → debugger) on completion.
            verbose: Print results to stdout.
        """
        super().__init__()
        self.auto_diagnose = auto_diagnose
        self.auto_pipeline = auto_pipeline
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

        if self._llm_calls and self._llm_calls[-1]["type"] == "start":
            self._llm_calls[-1]["type"] = "complete"
            self._llm_calls[-1]["output"] = text
        else:
            self._llm_calls.append({
                "type": "complete",
                "output": text,
                "time": datetime.now().isoformat(),
            })

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
        if self._start_time is None:
            self._start_time = datetime.now()
            # Capture user input
            if isinstance(inputs, dict):
                self._user_input = (
                    inputs.get("input", "")
                    or inputs.get("query", "")
                    or str(next(iter(inputs.values()), ""))
                )
            elif isinstance(inputs, str):
                self._user_input = inputs

    def on_chain_end(self, outputs, **kwargs):
        self._end_time = datetime.now()
        if isinstance(outputs, dict):
            self._final_output = (
                outputs.get("output", "")
                or outputs.get("response", "")
                or str(next(iter(outputs.values()), ""))
            )
        elif isinstance(outputs, str):
            self._final_output = outputs

        # Auto-diagnose on completion
        if self.auto_diagnose or self.auto_pipeline:
            self._run_diagnosis()

    def on_chain_error(self, error, **kwargs):
        self._chain_errors.append(str(error))
        self._end_time = datetime.now()

        if self.auto_diagnose or self.auto_pipeline:
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
        return {"ambiguity_score": min(1.0, round(score, 2))}

    def _build_interaction(self) -> dict:
        clarification = False
        for call in self._llm_calls:
            output = call.get("output", "")
            if output and any(m in output.lower() for m in
                              ["could you clarify", "did you mean",
                               "can you specify", "what do you mean"]):
                clarification = True
                break

        return {
            "clarification_triggered": clarification,
            "user_correction_detected": False,  # Cannot detect from callback alone
        }

    def _build_reasoning(self) -> dict:
        llm_complete = [c for c in self._llm_calls if c.get("type") == "complete"]
        replanned = False
        if len(llm_complete) >= 2:
            for call in llm_complete[1:]:
                output = call.get("output", "")
                if any(w in output.lower() for w in
                       ["let me try", "actually", "correction",
                        "different approach", "reconsider"]):
                    replanned = True
                    break
        return {"replanned": replanned}

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

    def _build_retrieval(self) -> dict:
        if not self._retriever_results:
            return {"skipped": True}
        return {"skipped": False}

    def _build_response(self) -> dict:
        return {"alignment_score": self._compute_alignment()}

    def _build_tools(self) -> dict:
        calls = [(c["name"], c.get("input", "")) for c in self._tool_calls]
        call_counts = Counter(calls)
        max_repeat = max(call_counts.values()) if call_counts else 0
        repeat_count = max_repeat - 1 if max_repeat > 1 else 0
        error_count = sum(1 for c in self._tool_calls if c.get("error"))

        return {
            "call_count": len(self._tool_calls),
            "repeat_count": repeat_count,
            "unique_tools": len(set(c["name"] for c in self._tool_calls)) if self._tool_calls else 0,
            "error_count": error_count,
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
        query_words = set(self._user_input.lower().split())
        response_words = set(self._final_output.lower().split())
        if not query_words:
            return 0.5
        return round(min(1.0, len(query_words & response_words) / len(query_words)), 2)

    # ---- Diagnosis ----

    def _run_diagnosis(self):
        """Run matcher (and optionally full pipeline) on collected telemetry."""
        telemetry = self.build_telemetry()

        # Save for inspection
        self.last_telemetry = telemetry

        try:
            from matcher import run as run_matcher

            # Run all patterns
            tmp_path = "/tmp/atlas_callback_telemetry.json"
            with open(tmp_path, "w") as f:
                json.dump(telemetry, f)

            failures_dir = ATLAS_ROOT / "failures"
            diagnosed = []
            for pf in sorted(failures_dir.glob("*.yaml")):
                result = run_matcher(str(pf), tmp_path)
                if result.get("diagnosed"):
                    diagnosed.append(result)

            self.last_diagnosed = diagnosed

            if self.verbose:
                print(f"\n{'='*50}")
                print(f"  Atlas Auto-Diagnosis ({len(diagnosed)} failures detected)")
                print(f"{'='*50}")
                for d in diagnosed:
                    print(f"  ✅ {d['failure_id']:40s} conf={d['confidence']}")
                if not diagnosed:
                    print("  No failures detected.")

            # Full pipeline if requested
            if self.auto_pipeline and diagnosed:
                self._run_pipeline(diagnosed)

        except ImportError as e:
            if self.verbose:
                print(f"\n⚠ Atlas auto-diagnosis unavailable: {e}")

    def _run_pipeline(self, diagnosed: list):
        """Run full debugger pipeline on diagnosed failures."""
        try:
            debugger_root = ATLAS_ROOT.parent / "agent-failure-debugger"
            if debugger_root.exists():
                sys.path.insert(0, str(debugger_root))

            from pipeline import run_pipeline
            result = run_pipeline(diagnosed, use_learning=True, top_k=1)
            self.last_pipeline_result = result
            s = result["summary"]

            if self.verbose:
                print(f"\n  Root cause:  {s['root_cause']} (conf={s['root_confidence']})")
                print(f"  Failures:    {s['failure_count']}")
                print(f"  Fixes:       {s['fix_count']}")
                print(f"  Gate:        {s['gate_mode']} (score={s['gate_score']})")

        except ImportError as e:
            if self.verbose:
                print(f"\n⚠ Debugger pipeline unavailable: {e}")

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
