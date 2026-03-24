# Observation Layer Gap Analysis: LangGraph vs CrewAI

## Purpose

This document records which observation layer heuristics are
**framework-specific** and which are **universal**, discovered by
implementing adapters for two architecturally different frameworks.

## Framework Comparison

| Concept | LangGraph | CrewAI |
|---|---|---|
| Execution unit | Chain (node in graph) | Task (assigned to agent) |
| Agent model | Implicit (LLM + tools) | Explicit (role, goal, backstory) |
| Input format | Messages (HumanMessage) | Task.description + expected_output |
| Tool integration | LangChain tools + callbacks | CrewAI tools + event bus |
| Callback system | BaseCallbackHandler | BaseEventListener + event bus |
| State management | TypedDict state graph | Crew-level memory + task context |
| Success criteria | Implicit (user satisfaction) | Explicit (expected_output) |

## Heuristic Classification

### Universal (works across frameworks)

| Heuristic | Method | Why universal |
|---|---|---|
| Tool repeat detection | Count by tool name | Any framework has named tools |
| Tool error counting | Count error events | Any framework reports errors |
| Negation in output | Keyword scan | Language-level, not framework-level |
| State progress from tool results | All results negative → no progress | Tool output semantics are universal |

### Framework-specific (requires adaptation)

| Heuristic | LangGraph implementation | CrewAI adaptation | Gap |
|---|---|---|---|
| User input extraction | `HumanMessage.content` from messages | `Task.description` (first task) | Input concept is fundamentally different |
| Alignment score | user_input vs final_output overlap | `expected_output` vs actual output | CrewAI is STRONGER (explicit target) |
| Correction detection | Response admits failure + topic pivot | Task failure event OR alignment < 0.3 | CrewAI has explicit failure signals |
| Clarification detection | Phrases in LLM output | Not applicable (no user in loop) | Structural gap — CrewAI is batch, not conversational |
| Chain depth tracking | `_chain_depth` counter | Not needed (Crew lifecycle events) | Architecture-specific |
| Hypothesis branching | Branching phrases in output | Agent re-execution count | Different proxy, same concept |

### Not applicable (gap)

| Heuristic | Why not applicable in CrewAI |
|---|---|
| Cache hit detection | CrewAI manages its own cache internally; not exposed via events |
| Retrieval skip detection | CrewAI's RAG is tool-based, not a separate retriever concept |
| Query intent similarity | No explicit query-document relationship in CrewAI's model |

## Key Finding: Alignment Score

The alignment heuristic reveals the most important architectural difference:

- **LangGraph:** No explicit success criteria. Alignment is guessed from word overlap
  between user query and agent response. Fragile — required topic mismatch penalty
  and negation detection to work at all.

- **CrewAI:** `expected_output` is a first-class concept. Alignment can be computed
  by comparing actual output to the expected output string. Structurally stronger.

**Implication:** Future adapters should always look for explicit success criteria
in the target framework. When available, they produce more reliable alignment
scores than the word-overlap heuristic.

## Key Finding: Correction Detection

- **LangGraph:** Must be inferred from response content (admits failure + pivots).
  Fragile, language-dependent.

- **CrewAI:** `TaskFailedEvent` provides explicit failure signal. Much more reliable.

**Implication:** Frameworks with explicit failure events are easier to diagnose.
The observation layer should prefer structural signals over language inference
whenever available.

## Recommendations

1. **BaseAdapter should document which telemetry fields are "best-effort"**
   and which are "structural." Downstream consumers (matcher) should weight
   structural signals higher.

2. **Alignment score computation should be adapter-specific**, not a shared
   heuristic. Each framework has different access to success criteria.

3. **The core matcher is framework-agnostic.** It processes the same telemetry
   JSON regardless of source. This is confirmed: no matcher changes were needed
   for CrewAI support.