"""
crewai_adapter.py

Phase 24+: CrewAI event-based adapter for Atlas matcher.

CrewAI uses a completely different architecture from LangChain/LangGraph:
  - No BaseCallbackHandler (LangChain-specific)
  - Uses BaseEventListener + crewai_event_bus
  - Role/Task/Crew structure instead of Chain/Tool/Message
  - Task.expected_output provides explicit alignment targets

This adapter collects CrewAI events and builds Atlas matcher input.
It also validates which observation layer heuristics are framework-
specific and which are universal.

Usage (Event Listener - recommended):

    from adapters.crewai_adapter import AtlasCrewListener

    listener = AtlasCrewListener(auto_diagnose=True)
    # Just instantiate — it auto-registers on the event bus

    crew = Crew(agents=[...], tasks=[...])
    crew.kickoff()
    # → diagnosis prints automatically on completion

Usage (Post-hoc from CrewOutput):

    from adapters.crewai_adapter import CrewAIAdapter

    adapter = CrewAIAdapter()
    matcher_input = adapter.from_crew_output(crew_output, tasks)

Requirements:
    pip install crewai
    The core atlas/debugger pipeline still requires only pyyaml.
"""

import json
import os
from collections import Counter
from pathlib import Path
from datetime import datetime

from llm_failure_atlas.adapters.base_adapter import BaseAdapter

# CrewAI imports (optional — adapter can be imported without crewai)
try:
    from crewai.events import (
        crewai_event_bus,
        AgentExecutionStartedEvent,
        AgentExecutionCompletedEvent,
        AgentExecutionErrorEvent,
        TaskStartedEvent,
        TaskCompletedEvent,
        TaskFailedEvent,
        ToolUsageStartedEvent,
        ToolUsageFinishedEvent,
        ToolUsageErrorEvent,
        CrewKickoffStartedEvent,
        CrewKickoffCompletedEvent,
    )
    from crewai.events.base_event_listener import BaseEventListener
    HAS_CREWAI = True
except ImportError:
    HAS_CREWAI = False


# ---------------------------------------------------------------------------
# Post-hoc adapter (from CrewOutput / task list)
# ---------------------------------------------------------------------------

class CrewAIAdapter(BaseAdapter):
    """
    Convert CrewAI execution results into Atlas matcher input.

    Unlike the LangChain adapter which works on trace JSON,
    this adapter works on CrewAI's native output objects.
    """

    source = "crewai"

    def normalize(self, raw_log: dict) -> dict:
        """Normalize CrewAI execution data."""
        return raw_log  # Already structured by from_crew_output

    def extract_features(self, normalized: dict) -> dict:
        """Build matcher-compatible telemetry from normalized CrewAI data."""
        return {
            "input": normalized.get("input", {"ambiguity_score": 0.3}),
            "interaction": normalized.get("interaction", {
                "clarification_triggered": False,
                "user_correction_detected": False,
            }),
            "reasoning": normalized.get("reasoning", {
                "replanned": False,
                "hypothesis_count": 1,
            }),
            "cache": normalized.get("cache", {
                "hit": False, "similarity": 0.0, "query_intent_similarity": 1.0,
            }),
            "retrieval": normalized.get("retrieval", {"skipped": True}),
            "response": normalized.get("response", {"alignment_score": 0.5}),
            "tools": normalized.get("tools", {
                "call_count": 0, "repeat_count": 0,
                "unique_tools": 0, "error_count": 0,
            }),
            "state": normalized.get("state", {"progress_made": True}),
        }

    def from_crew_output(self, crew_output, tasks=None) -> dict:
        """
        Build matcher input from CrewAI's CrewOutput + task list.

        Args:
            crew_output: CrewOutput object from crew.kickoff()
            tasks: list of Task objects (for expected_output comparison)
        """
        telemetry = {}

        # --- Input ambiguity ---
        # CrewAI tasks have explicit descriptions — ambiguity is lower by design
        telemetry["input"] = {"ambiguity_score": 0.2}

        # --- Alignment ---
        # CrewAI advantage: expected_output is explicitly defined
        alignment = self._compute_task_alignment(crew_output, tasks)
        telemetry["response"] = {"alignment_score": alignment}

        # --- Interaction ---
        telemetry["interaction"] = {
            "clarification_triggered": False,
            "user_correction_detected": alignment < 0.3,  # Very low alignment = implicit correction needed
        }

        # --- Tools ---
        task_outputs = getattr(crew_output, "tasks_output", [])
        tool_info = self._extract_tool_info(task_outputs)
        telemetry["tools"] = tool_info

        # --- Reasoning ---
        telemetry["reasoning"] = {
            "replanned": False,  # CrewAI doesn't expose replanning
            "hypothesis_count": 1,
        }

        # --- State ---
        failed_tasks = sum(1 for t in task_outputs if self._is_task_failed(t))
        telemetry["state"] = {
            "progress_made": failed_tasks == 0,
        }

        # --- Cache / Retrieval ---
        telemetry["cache"] = {"hit": False, "similarity": 0.0, "query_intent_similarity": 1.0}
        telemetry["retrieval"] = {"skipped": True}

        return self.extract_features(telemetry)

    def _compute_task_alignment(self, crew_output, tasks) -> float:
        """
        Compare final output against expected_output of last task.

        This is STRONGER than the LangGraph heuristic because
        CrewAI explicitly defines what "correct" looks like.
        """
        if not tasks:
            return 0.5

        last_task = tasks[-1]
        expected = getattr(last_task, "expected_output", "") or ""
        actual = getattr(crew_output, "raw", "") or ""

        if not expected or not actual:
            return 0.5

        # Keyword overlap between expected and actual
        expected_words = set(expected.lower().split())
        actual_words = set(actual.lower().split())

        if not expected_words:
            return 0.5

        overlap = len(expected_words & actual_words) / len(expected_words)

        # Negation penalty
        negation_markers = ["couldn't", "unable", "failed", "error", "no results"]
        if any(m in actual.lower() for m in negation_markers):
            overlap *= 0.5

        return round(min(1.0, overlap), 2)

    def _extract_tool_info(self, task_outputs) -> dict:
        """Extract tool usage patterns from task outputs."""
        # CrewAI doesn't directly expose tool calls in TaskOutput
        # This is a structural gap — event listener provides better data
        return {
            "call_count": 0,
            "repeat_count": 0,
            "unique_tools": 0,
            "error_count": 0,
        }

    def _is_task_failed(self, task_output) -> bool:
        """Check if a task output indicates failure."""
        raw = getattr(task_output, "raw", "") or ""
        return any(m in raw.lower() for m in [
            "failed", "error", "unable to complete",
            "could not", "no results",
        ])


# ---------------------------------------------------------------------------
# Real-time event listener
# ---------------------------------------------------------------------------

if HAS_CREWAI:
    class AtlasCrewListener(BaseEventListener):
        """
        CrewAI event listener that collects execution events
        and builds Atlas matcher input automatically.

        Collects:
          - Agent execution events (start, complete, error)
          - Task events (start, complete, fail)
          - Tool usage events (start, finish, error)
          - Crew lifecycle events (kickoff, complete)

        On crew completion, builds telemetry and optionally
        runs the full matcher → debugger pipeline.
        """

        def __init__(self, auto_diagnose: bool = False,
                     auto_pipeline: bool = False,
                     verbose: bool = True):
            super().__init__()
            self.auto_diagnose = auto_diagnose
            self.auto_pipeline = auto_pipeline
            self.verbose = verbose
            self._reset_state()

        def _reset_state(self):
            self._agent_executions = []
            self._task_events = []
            self._tool_calls = []
            self._errors = []
            self._crew_name = ""
            self._start_time = None
            self._end_time = None
            self._task_descriptions = []
            self._task_expected_outputs = []
            self._final_output = ""

        def setup_listeners(self, crewai_event_bus):
            """Required by BaseEventListener. Registers handlers on the CrewAI event bus."""

            @crewai_event_bus.on(CrewKickoffStartedEvent)
            def on_crew_start(source, event):
                self._start_time = datetime.now()
                self._crew_name = getattr(event, "crew_name", "")

            @crewai_event_bus.on(CrewKickoffCompletedEvent)
            def on_crew_complete(source, event):
                self._end_time = datetime.now()
                self._final_output = getattr(event, "output", "")
                if self.auto_diagnose or self.auto_pipeline:
                    self._run_diagnosis()

            @crewai_event_bus.on(AgentExecutionStartedEvent)
            def on_agent_start(source, event):
                self._agent_executions.append({
                    "type": "start",
                    "agent": getattr(event, "agent_name", "unknown"),
                    "time": datetime.now().isoformat(),
                })

            @crewai_event_bus.on(AgentExecutionCompletedEvent)
            def on_agent_complete(source, event):
                self._agent_executions.append({
                    "type": "complete",
                    "agent": getattr(event, "agent_name", "unknown"),
                    "output": str(getattr(event, "output", "")),
                    "time": datetime.now().isoformat(),
                })

            @crewai_event_bus.on(AgentExecutionErrorEvent)
            def on_agent_error(source, event):
                self._errors.append({
                    "source": "agent",
                    "agent": getattr(event, "agent_name", "unknown"),
                    "error": str(getattr(event, "error", "")),
                    "time": datetime.now().isoformat(),
                })

            @crewai_event_bus.on(TaskStartedEvent)
            def on_task_start(source, event):
                desc = getattr(event, "description", "")
                expected = getattr(event, "expected_output", "")
                self._task_events.append({
                    "type": "start",
                    "description": desc,
                    "expected_output": expected,
                    "time": datetime.now().isoformat(),
                })
                self._task_descriptions.append(desc)
                self._task_expected_outputs.append(expected)

            @crewai_event_bus.on(TaskCompletedEvent)
            def on_task_complete(source, event):
                self._task_events.append({
                    "type": "complete",
                    "output": str(getattr(event, "output", "")),
                    "time": datetime.now().isoformat(),
                })

            @crewai_event_bus.on(TaskFailedEvent)
            def on_task_failed(source, event):
                self._task_events.append({
                    "type": "failed",
                    "error": str(getattr(event, "error", "")),
                    "time": datetime.now().isoformat(),
                })

            @crewai_event_bus.on(ToolUsageStartedEvent)
            def on_tool_start(source, event):
                self._tool_calls.append({
                    "name": getattr(event, "tool_name", "unknown"),
                    "input": str(getattr(event, "input", "")),
                    "output": None,
                    "error": None,
                    "time": datetime.now().isoformat(),
                })

            @crewai_event_bus.on(ToolUsageFinishedEvent)
            def on_tool_finish(source, event):
                if self._tool_calls and self._tool_calls[-1]["output"] is None:
                    self._tool_calls[-1]["output"] = str(getattr(event, "output", ""))

            @crewai_event_bus.on(ToolUsageErrorEvent)
            def on_tool_error(source, event):
                if self._tool_calls:
                    self._tool_calls[-1]["error"] = str(getattr(event, "error", ""))
                self._errors.append({
                    "source": "tool",
                    "error": str(getattr(event, "error", "")),
                    "time": datetime.now().isoformat(),
                })

        # ---- Telemetry building ----

        def build_telemetry(self) -> dict:
            return {
                "input": self._build_input(),
                "interaction": self._build_interaction(),
                "reasoning": self._build_reasoning(),
                "cache": {"hit": False, "similarity": 0.0, "query_intent_similarity": 1.0},
                "retrieval": {"skipped": True},
                "response": self._build_response(),
                "tools": self._build_tools(),
                "state": self._build_state(),
            }

        def _build_input(self) -> dict:
            # CrewAI tasks have explicit descriptions → lower ambiguity
            # But if task descriptions are very short, ambiguity rises
            if self._task_descriptions:
                avg_len = sum(len(d.split()) for d in self._task_descriptions) / len(self._task_descriptions)
                score = 0.2 if avg_len > 10 else 0.4 if avg_len > 5 else 0.6
            else:
                score = 0.5
            return {"ambiguity_score": round(score, 2)}

        def _build_interaction(self) -> dict:
            # CrewAI: no user messages within execution
            # Clarification = agent asking for delegation (different concept)
            clarification = False

            # Correction inferred: task failed or output doesn't match expected
            correction_needed = False
            task_completes = [t for t in self._task_events if t["type"] == "complete"]
            task_fails = [t for t in self._task_events if t["type"] == "failed"]

            if task_fails:
                correction_needed = True
            elif task_completes and self._task_expected_outputs:
                last_output = task_completes[-1].get("output", "")
                last_expected = self._task_expected_outputs[-1] if self._task_expected_outputs else ""
                if last_expected and last_output:
                    alignment = self._word_alignment(last_expected, last_output)
                    if alignment < 0.3:
                        correction_needed = True

            return {
                "clarification_triggered": clarification,
                "user_correction_detected": correction_needed,
            }

        def _build_reasoning(self) -> dict:
            # Detect replanning from agent execution patterns
            # If the same agent executes multiple times, it may indicate iteration
            agent_counts = Counter(
                e["agent"] for e in self._agent_executions if e["type"] == "start"
            )
            max_agent_runs = max(agent_counts.values()) if agent_counts else 0

            replanned = False
            for e in self._agent_executions:
                if e["type"] == "complete":
                    output = e.get("output", "").lower()
                    if any(w in output for w in ["let me reconsider", "actually",
                                                  "different approach", "try again"]):
                        replanned = True
                        break

            return {
                "replanned": replanned,
                "hypothesis_count": min(max_agent_runs, 3) if max_agent_runs > 1 else 1,
            }

        def _build_response(self) -> dict:
            # CrewAI advantage: expected_output is explicit
            if not self._task_expected_outputs or not self._final_output:
                return {"alignment_score": 0.5}

            last_expected = self._task_expected_outputs[-1]
            alignment = self._word_alignment(last_expected, str(self._final_output))
            return {"alignment_score": alignment}

        def _build_tools(self) -> dict:
            name_counts = Counter(c["name"] for c in self._tool_calls)
            max_repeat = max(name_counts.values()) if name_counts else 0
            repeat_count = max_repeat - 1 if max_repeat > 1 else 0
            error_count = sum(1 for c in self._tool_calls if c.get("error"))

            return {
                "call_count": len(self._tool_calls),
                "repeat_count": repeat_count,
                "unique_tools": len(name_counts) if name_counts else 0,
                "error_count": error_count,
            }

        def _build_state(self) -> dict:
            task_fails = [t for t in self._task_events if t["type"] == "failed"]
            if task_fails:
                return {"progress_made": False}

            # Check if tool results are all negative
            if self._tool_calls:
                name_counts = Counter(c["name"] for c in self._tool_calls)
                most_called = name_counts.most_common(1)[0] if name_counts else None
                if most_called and most_called[1] >= 2:
                    tool_name = most_called[0]
                    outputs = [c.get("output", "") for c in self._tool_calls if c["name"] == tool_name]
                    negative_markers = ["no ", "not found", "empty", "error", "none", "failed"]
                    all_negative = all(
                        any(m in str(o).lower() for m in negative_markers) for o in outputs if o
                    )
                    if all_negative:
                        return {"progress_made": False}

            return {"progress_made": True}

        def _word_alignment(self, expected: str, actual: str) -> float:
            """Word overlap alignment with negation penalty."""
            if not expected or not actual:
                return 0.5
            expected_words = set(expected.lower().split())
            actual_words = set(actual.lower().split())
            if not expected_words:
                return 0.5
            overlap = len(expected_words & actual_words) / len(expected_words)

            negation_markers = ["couldn't", "unable", "failed", "error", "no results",
                                "unfortunately", "could not"]
            if any(m in actual.lower() for m in negation_markers):
                overlap *= 0.5

            return round(min(1.0, overlap), 2)

        # ---- Diagnosis ----

        META_PATTERNS = {"unmodeled_failure", "insufficient_observability", "conflicting_signals"}

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
            """2-pass diagnosis: domain patterns then meta patterns."""
            telemetry = self.build_telemetry()
            self.last_telemetry = telemetry

            try:
                from llm_failure_atlas.matcher import run as run_matcher
                from llm_failure_atlas.resource_loader import get_patterns_dir
                import tempfile

                tmp_path = os.path.join(tempfile.gettempdir(), "atlas_crewai_telemetry.json")
                failures_dir = Path(get_patterns_dir())

                # Pass 1: domain
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(telemetry, f)

                domain_diagnosed = []
                for pf in sorted(failures_dir.glob("*.yaml")):
                    if pf.stem in self.META_PATTERNS:
                        continue
                    result = run_matcher(str(pf), tmp_path)
                    if result.get("diagnosed"):
                        domain_diagnosed.append(result)

                # Pass 2: meta
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
                    print(f"  Atlas CrewAI Diagnosis ({len(diagnosed)} failures)")
                    print(f"{'='*50}")
                    for d in diagnosed:
                        tag = " [meta]" if d["failure_id"] in self.META_PATTERNS else ""
                        print(f"  ✅ {d['failure_id']:40s} conf={d['confidence']}{tag}")
                    if not diagnosed:
                        print("  No failures detected.")

                if self.auto_pipeline and diagnosed:
                    self._run_pipeline(diagnosed)

            except ImportError as e:
                if self.verbose:
                    print(f"\n⚠ Atlas diagnosis unavailable: {e}")

        def _run_pipeline(self, diagnosed: list):
            try:
                from agent_failure_debugger.pipeline import run_pipeline
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

        def get_events(self) -> dict:
            return {
                "agent_executions": self._agent_executions,
                "task_events": self._task_events,
                "tool_calls": self._tool_calls,
                "errors": self._errors,
                "final_output": str(self._final_output),
            }


# ---------------------------------------------------------------------------
# CLI (post-hoc from JSON log)
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for processing CrewAI JSON logs."""
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not args:
        print("Usage: python crewai_adapter.py crew_output.json [--with-metadata]")
        print("\nCrewAI adapter for Atlas matcher.")
        print("Accepts JSON export of CrewAI execution results.")
        sys.exit(1)

    with open(args[0], encoding="utf-8") as f:
        raw_log = json.load(f)

    adapter = CrewAIAdapter()
    normalized = adapter.normalize(raw_log)

    if "--with-metadata" in sys.argv:
        result = {"telemetry": adapter.extract_features(normalized), "source": "crewai"}
    else:
        result = adapter.extract_features(normalized)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()