# API Orchestration Example — Travel Planner Service Failure

This example demonstrates Atlas on a multi-API orchestration scenario with no LLM involved. A travel planning service calls multiple external APIs, one of which fails repeatedly, leading to an error-driven shutdown.

## Scenario

A travel planner service orchestrates three API calls to build a trip package:

```
API 1: search_hotels      → success (3 results)
API 2: search_flights      → failure ("service unavailable")
API 3: search_flights      → retry failure
API 4: search_flights      → retry failure → raises exception
API 5: search_car_rental   → success (2 results) — never reached
Result: service terminates with error, no output produced
```

The flight API fails repeatedly. The orchestrator retries without changing strategy (no fallback provider, no partial result return). After the third failure, the service raises an exception and terminates.

## What Atlas Detects

| Pattern | Confidence | Signals |
|---|---|---|
| `agent_tool_call_loop` | 0.7 | `repeated_tool_call_without_progress`, `no_replanning_before_repeat` |
| `failed_termination` | 0.7 | `execution_error_caused_termination`, `error_without_output` |

**Root cause:** `agent_tool_call_loop` (score: 0.85)

**Causal chain:**

```
agent_tool_call_loop  →  failed_termination
     (root cause)          (downstream symptom)
```

The retry loop exhausted the error budget and caused an exception, which terminated the service without output.

## Comparison with workflow_pipeline Example

Both examples use the same root cause but produce different downstream effects. The branching point is a single telemetry field:

| | workflow_pipeline | api_orchestration |
|---|---|---|
| Root cause | `agent_tool_call_loop` | `agent_tool_call_loop` |
| **`state.chain_error_occurred`** | **`false`** | **`true`** |
| Downstream | `premature_termination` (silent exit) | `failed_termination` (error exit) |
| Output produced | No | No |
| Scenario | Payment step retries silently | Flight API retries then throws |

The causal graph has two edges from `agent_tool_call_loop`: one to `premature_termination` (when retries exhaust silently) and one to `failed_termination` (when retries raise an exception). Which path activates depends on whether the system raised an error (`chain_error_occurred`).

## Mapping: API Orchestration → Atlas Telemetry

| Atlas concept | LLM agent meaning | API orchestration meaning |
|---|---|---|
| `tools.call_count` | Number of tool invocations | Number of API calls |
| `tools.repeat_count` | Same tool called with same args | Same API endpoint retried |
| `tools.error_count` | Tool exceptions | API exceptions |
| `state.any_tool_looping` | Tool called 3+ times, 0 successes | API retried 3+ times, all failures |
| `state.progress_made` | At least one tool returned data | At least one API returned data |
| `state.chain_error_occurred` | Agent raised an exception | Service raised an exception |
| `state.output_produced` | Agent generated a response | Service returned a result |
| `reasoning.replanned` | Agent changed approach | Orchestrator switched to fallback |

## What This Proves

1. **API orchestration failures map directly to Atlas patterns.** Retry loops, error handling, and termination behavior are the same whether the caller is an LLM agent or a service orchestrator.
2. **The causal graph distinguishes silent vs error termination.** `premature_termination` (no output, no error) and `failed_termination` (no output, error raised) have different root-cause implications and different fix strategies.
3. **The fix proposal is domain-appropriate.** The debugger recommends adding a max repeat limit with progress validation — which is exactly the standard resilience pattern (circuit breaker / retry budget) used in API orchestration.

## How to Run

```bash
python -m agent_failure_debugger.main examples/api_orchestration/matcher_output.json
```