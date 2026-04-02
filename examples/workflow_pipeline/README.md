# Workflow Pipeline Example — Order Processing Failure

This example demonstrates that Atlas's failure patterns are not limited to LLM agents. The same causal graph and matcher work on traditional workflow/pipeline failures where steps retry without progress and the pipeline terminates without completing.

## Scenario

An order processing pipeline with three steps:

```
Step 1: check_inventory   → success (item in stock)
Step 2: process_payment   → failure (payment gateway timeout)
Step 3: process_payment   → retry failure
Step 4: process_payment   → retry failure
Step 5: process_payment   → retry failure
Step 6: create_shipment   → never reached
```

The payment step fails repeatedly, the pipeline does not reroute or escalate, and execution stops without producing a result.

## What Atlas Detects

| Pattern | Confidence | Signals |
|---|---|---|
| `agent_tool_call_loop` | 0.7 | `repeated_tool_call_without_progress`, `no_replanning_before_repeat` |
| `premature_termination` | 0.75 | `silent_exit_without_output`, `tools_called_but_no_output` |

**Root cause:** `agent_tool_call_loop` (score: 0.85)

**Causal chain:**

```
agent_tool_call_loop  →  premature_termination
     (root cause)            (downstream symptom)
```

The pipeline retried the same failing step without changing strategy, exhausted its execution budget, and stopped without completing the workflow.

## Mapping: Workflow Concepts → Atlas Telemetry

The key insight is that Atlas's telemetry fields have natural counterparts in workflow systems:

| Atlas concept | LLM agent meaning | Workflow meaning |
|---|---|---|
| `tools.call_count` | Number of tool invocations | Number of step executions |
| `tools.repeat_count` | Same tool called with same args | Same step retried |
| `state.any_tool_looping` | Tool called 3+ times, 0 successes | Step retried 3+ times, all failures |
| `state.progress_made` | At least one tool returned data | At least one step completed |
| `state.output_produced` | Agent generated a response | Pipeline produced a final result |
| `state.chain_error_occurred` | Agent raised an exception | Pipeline raised an exception |
| `reasoning.replanned` | Agent changed approach after failure | Pipeline rerouted to alternative step |

## What This Proves

1. **No code changes needed.** The same matcher, patterns, and causal graph work on both LLM agent traces and workflow traces.
2. **The failure taxonomy is not LLM-specific.** "Retry without progress" and "silent termination" are universal failure modes that occur in any system with sequential steps and error handling.
3. **The causal graph adds value.** Without the graph, you'd see two independent failures. With the graph, you see that the retry loop *caused* the premature termination — which points to the fix (add a retry limit or fallback strategy, not just handle the termination).

## How to Run

```bash
# Detection only
python -m agent_failure_debugger.main examples/workflow_pipeline/matcher_output.json

# Full pipeline (from telemetry)
python -c "
from agent_failure_debugger.pipeline import run_pipeline
import json

with open('examples/workflow_pipeline/matcher_output.json') as f:
    matcher_output = json.load(f)

result = run_pipeline(matcher_output, use_learning=True, include_explanation=True)
print(json.dumps(result['summary'], indent=2))
print(result.get('explanation', {}).get('context_summary', ''))
"
```

## Other Workflow Failures This Approach Can Detect

With appropriate telemetry mapping, Atlas can also detect:

- **`failed_termination`** — pipeline step throws an unhandled exception and halts (map: `state.chain_error_occurred=True`)
- **`incorrect_output`** — pipeline completes but produces a result that doesn't match the request (map: `response.alignment_score < 0.5` + downstream validation)
- **`clarification_failure`** — pipeline receives ambiguous input and proceeds without validation (map: `input.ambiguity_score > 0.7` + `interaction.clarification_triggered=False`)