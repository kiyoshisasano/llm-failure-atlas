# Phase 2 Experiment #1: Cross-Model Validation (Claude Haiku)

## Experiment Setup

- **Model under test:** claude-haiku-4-5-20251001 (temperature=0, max_tokens=1024)
- **Baseline model:** gpt-4o-mini (Stage 1 results)
- **Runtime:** LangGraph + Atlas watch() callback handler
- **Date:** 2026-03-31

## Motivation

All Stage 1 testing used gpt-4o-mini exclusively. This experiment validates
that Atlas detection logic works correctly across different LLMs, and documents
behavioral differences that affect telemetry extraction.

---

## Experiment #1a: Stage 1 Scenario Reproduction

Reproduced the 3 Stage 1 scenarios with Claude Haiku using identical agent
structure and tools.

### Results

| Scenario | gpt-4o-mini | Claude Haiku | Correct |
|---|---|---|---|
| flight_hotel_pivot | incorrect_output (0.7) | No failure detected | ✅ |
| tool_loop | agent_tool_call_loop (0.7) | No failure detected | ✅ |
| ambiguous_cancel | clarification_failure (0.7) | No failure detected | ✅ |

**0/3 match — but all 3 are correct.** Claude Haiku did not produce the
failures, so Atlas correctly reported 0 diagnosed failures.

### What happened

**flight_hotel_pivot:** Claude asked for date clarification instead of calling
search_flights. No tool was called, no pivot occurred. The failure never happened.

**tool_loop:** Claude called check_inventory once, received an error, and
reported the failure to the user. No retry loop occurred.

**ambiguous_cancel:** Claude responded with "I need the booking ID. Could you
please provide..." — this is genuine clarification, so clarification_failure
should not fire. Atlas correctly detected clarification_triggered=True and
did not diagnose a failure.

---

## Experiment #1b: Claude-Specific Failure Scenarios

Since Claude's default behavior avoids the failure modes that gpt-4o-mini
triggers naturally, 4 scenarios with system prompts that force failure-inducing
behavior were designed.

### Results

| Scenario | Expected | Detected | Verdict |
|---|---|---|---|
| forced_pivot | incorrect_output | incorrect_output (0.7) | ✅ PASS |
| forced_retry_loop | agent_tool_call_loop | agent_tool_call_loop (0.7) | ✅ PASS |
| no_clarification_allowed | clarification_failure | clarification_failure (0.7) | ✅ PASS |
| grounding_gap | incorrect_output | None | ❌ MISS |

**3/4 PASS.** The single MISS (grounding_gap) is a known observation gap.

### Scenario details

**forced_pivot:** System prompt instructed "always provide alternatives when
primary search fails." Claude pivoted from flights to hotels. Atlas correctly
detected incorrect_output via alignment mismatch and topic pivot.

**forced_retry_loop:** System prompt instructed "retry at least 3 times before
giving up." Claude made 4 tool calls (repeat_count=3, progress_made=False).
Atlas correctly detected agent_tool_call_loop.

**no_clarification_allowed:** System prompt instructed "never ask clarifying
questions, make your best guess." Claude cancelled booking DEFAULT-001 without
clarification. Atlas correctly detected clarification_failure.

**grounding_gap (MISS):** Tool returned "weather API is down" but system prompt
demanded a detailed forecast. Claude fabricated specific temperatures and
precipitation chances, prefacing with "Based on typical weather patterns."

Telemetry showed: tool_provided_data=False, expansion_ratio=inf,
uncertainty_acknowledged=True. The agent disclosed its lack of data but still
produced fabricated specifics. This is a concrete instance of the "thin
grounding" observation gap (documented in the handoff). The current pattern
set does not cover it.

---

## Experiment #3: False Positive Tests

7 healthy telemetry scenarios tested against all 15 patterns (no API required).

| Scenario | Description | Domain Failures |
|---|---|---|
| clean_simple | Normal response, no tools | 0 |
| clean_with_tools | Tools succeed, good alignment | 0 |
| clean_with_cache | Cache hit with matching intent | 0 |
| clean_clarification | Agent properly asks for clarification | 0 |
| clean_replanning | Agent replans after correction | 0 |
| high_ambiguity_but_clarified | Ambiguous input handled correctly | 0 |
| multiple_tools_no_repeat | 4 tools, no repeats, all succeed | 0 |

**7/7 PASS. No false positives on healthy telemetry.**

---

## Experiment #4: diagnose() Path Verification

The watch() path uses `callback_handler.py` to build telemetry directly from
LangChain callbacks. The diagnose() path uses `langchain_adapter.py` to convert
a raw trace dict. These are separate code paths that must produce identical
telemetry for the same execution.

### Initial result: 0/3 BOTH_PASS

All 3 forced scenarios detected failures via watch() but not via diagnose().
Root cause: `langchain_adapter.py` was missing several telemetry sections
that `callback_handler.py` produces:

| Missing field | Required by | Impact |
|---|---|---|
| state.progress_made | agent_tool_call_loop | Signal could not evaluate |
| grounding.* | incorrect_output | Grounding signals missing |
| reasoning.hypothesis_count | clarification_failure | Signal could not evaluate |
| soft_error_count | (state inference) | Progress inference inaccurate |
| topic-pivot alignment penalty | incorrect_output | alignment_score inflated |
| user_correction inference | incorrect_output | Topic pivot not detected |

### Fix: langchain_adapter parity

Added the missing extraction methods to `langchain_adapter.py`:

- `_extract_state()` — infers progress_made from tool output analysis
- `_extract_grounding()` — assesses evidence quality (tool_provided_data,
  uncertainty_acknowledged, expansion_ratio)
- `hypothesis_count` in `_extract_reasoning()` — detects branching markers
- `soft_error_count` in `_extract_tools()` — detects soft failures in outputs
- Topic-pivot penalty and negation penalty in `_estimate_alignment()`
- User correction inference in `_extract_interaction()` — detects topic pivot
  without explicit feedback

### Result after fix: 3/3 BOTH_PASS

| Scenario | watch() | diagnose() | Telemetry diff |
|---|---|---|---|
| forced_pivot | incorrect_output (0.7) | incorrect_output (0.7) | None |
| forced_retry_loop | agent_tool_call_loop (0.7) | agent_tool_call_loop (0.7) | None |
| no_clarification_allowed | clarification_failure (0.7) | clarification_failure (0.7) | None |

Both code paths now produce identical telemetry and identical diagnoses.

---

## Code Changes

Three issues were discovered and fixed during this experiment.

### 1. Replanning marker refinement

**Problem:** `_build_reasoning` treated "let me try" and "actually" as
replanning indicators. Claude uses these phrases before simple retries,
causing reasoning.replanned=True even when no actual strategy change occurred.
This suppressed the `no_replanning_before_repeat` signal and prevented
tool loop detection (confidence reached 0.4, threshold is 0.6).

**Fix:** Replaced the marker list with phrases that require explicit strategy
change ("different approach", "reconsider", "try a different", "change strategy",
"switch to"). Removed "let me try" and "actually".

Applied to: `callback_handler.py`, `langchain_adapter.py`.

### 2. Clarification marker expansion

**Problem:** `_build_interaction` only recognized explicit clarification
phrases ("could you clarify", "did you mean"). Claude's clarification style
("I need the booking ID", "Could you please provide") was not detected.
This caused clarification_triggered=False even when the agent was genuinely
asking for clarification, leading to a false positive on the ambiguous_cancel
scenario.

**Fix:** Added Claude-style markers: "could you provide", "i need the",
"i need to know", "please provide", "please specify", and others.

Applied to: `callback_handler.py`, `langchain_adapter.py`.

### 3. langchain_adapter telemetry parity

**Problem:** `langchain_adapter.py` was missing `state`, `grounding`,
`hypothesis_count`, `soft_error_count`, topic-pivot alignment penalty, and
user correction inference. This caused the diagnose() code path to produce
different (incomplete) telemetry compared to watch(), resulting in missed
detections.

**Fix:** Added `_extract_state()`, `_extract_grounding()`, hypothesis branching
detection, soft error counting, topic-pivot and negation penalties in alignment
scoring, and user correction inference via topic pivot detection. All logic
mirrors the corresponding callback_handler methods.

Applied to: `langchain_adapter.py`.

### Regression verification

After all fixes: 10/10 existing regression tests pass, 7/7 false positive
tests pass, 3/3 diagnose() path tests produce identical results to watch().

---

## Cross-Model Behavioral Differences

| Behavior | gpt-4o-mini | Claude Haiku |
|---|---|---|
| Ambiguous input | Guesses and executes immediately | Asks for clarification |
| Tool error | Retries multiple times | Reports failure after 1 attempt |
| Retry expression | Mechanically retries | Inserts "let me try" between retries |
| Missing data | Generates answer from training data | Acknowledges gap, then generates anyway |
| Topic pivot | Suggests alternatives unprompted | Does not pivot without instruction |

---

## Findings

### 1. Atlas detection logic is model-agnostic

When Claude produces a failure, Atlas detects it (3/4 in forced scenarios).
When Claude does not produce a failure, Atlas correctly reports 0. The
detection layer has no model-specific bugs.

### 2. Heuristic extraction required model-aware refinement

Two heuristics showed model sensitivity and were fixed:
- Replanning markers: over-broad, triggered on retry announcements
- Clarification markers: under-broad, missed Claude-style phrasing

Both fixes improved accuracy without regression.

### 3. Thin grounding gap confirmed with real data

The grounding_gap scenario produced a clear instance of fabricated output
(expansion_ratio=inf, uncertainty_acknowledged=True, tool_provided_data=False).
This validates observation gap #1 from the handoff document.

### 4. Test scenarios must account for model behavior

gpt-4o-mini triggers failures naturally under conditions where Claude Haiku
does not. Cross-model validation requires scenarios that control behavior
via system prompts. This is a property of the testing methodology, not of Atlas.