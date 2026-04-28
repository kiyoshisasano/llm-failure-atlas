# PoC: Chunk-Level Utilisation Tracking

This is a working proof-of-concept for the four-dimensional retrieval-set
diagnostic discussed in the LangChain Forum thread "When is it actually a
failure?".

It demonstrates the **behavioral side** of the joint diagnostic — specifically,
how the toolchain can emit a chunk-level **utilisation** signal that pairs
with ChunkScore's pre-ingestion quality and runtime coverage signals.

## What this PoC shows

Two scenarios are run end-to-end through the deterministic detection
and diagnosis pipeline (Atlas v0.1.5 + Debugger v0.4.0):

1. **Coverage-diversity failure** — five retrieved chunks all describe the
   same facet of the query (Q3 enterprise revenue numbers), the agent
   produces an ungrounded supplemental claim ("macroeconomic headwinds")
   that no chunk supports.
2. **Navigation failure** — five retrieved chunks include both numeric
   data and explanatory analyst notes, but the agent only uses the
   numeric chunks and ignores the analyst-note evidence.

Both scenarios are **constructed**: the raw logs are hand-written rather
than captured from a live agent. The intent is to make the failure modes
explicit and unambiguous so the diagnostic output is easy to interpret.
A real-LLM reproduction (gpt-4o-mini against the same context) actually
**did not** reproduce the navigation failure — the model used the analyst
note correctly. This is itself a useful finding: navigation failures
appear strongly model- and prompt-design-dependent, and the proxy
implementation is the right place to look for them, but the failure
itself is not as common as the spec discussion might imply.

## Files

| File | Purpose |
|---|---|
| `raw_log.json` | Scenario 1 input log (5 redundant chunks) |
| `raw_log_navigation.json` | Scenario 2 input log (5 mixed chunks) |
| `diagnose_output_scenario1.json` | Pipeline output for scenario 1 |
| `diagnose_output_scenario2.json` | Pipeline output for scenario 2 |
| `spec_v1_1_mapping.md` | Field-by-field mapping between Provenance Spec v1.1 and Atlas/Debugger output |
| `README.md` | This document |

## Running it yourself

```bash
pip install --upgrade llm-failure-atlas agent-failure-debugger
```

```python
import json
from agent_failure_debugger import diagnose

with open("raw_log_navigation.json") as f:
    raw_log = json.load(f)

result = diagnose(raw_log, adapter="langchain")

# Retrieval-side fields (adapter output)
retrieval = result["telemetry"]["retrieval"]
print(retrieval["retrieved_ids"])         # all chunks retrieved
print(retrieval["used_chunk_ids"])        # subset used (text-overlap proxy)
print(retrieval["utilisation_method"])    # 'text_overlap_proxy'

# Aggregated utilisation (execution_quality summary)
util = result["summary"]["execution_quality"]["utilisation"]
print(util)
# {'ratio': 0.4, 'used_count': 2, 'retrieved_count': 5,
#  'method': 'text_overlap_proxy'}
```

## What the output looks like

### Scenario 1 (coverage-diversity failure)

```
retrieved_ids:    [c1, c2, c3, c4, c5]   # 5 chunks, all about Q3 numbers
used_chunk_ids:   [c1, c2, c3, c4, c5]   # all flagged 'used' by the proxy
utilisation_ratio: 1.00
```

The proxy returns 1.0 because all five chunks share the same vocabulary
with the agent's response (the Q3 numbers). The proxy **cannot
distinguish** redundant retrieval from actual utilisation in this case.

This is a real limitation of any text-overlap proxy and is documented as
such. Coverage-diversity itself is **only visible from the retrieval
side** (ChunkScore can compute pairwise chunk similarity within the
retrieval set), so the two systems are complementary here: the
retrieval side flags the redundancy; the behavioral side cannot.

### Scenario 2 (navigation failure)

```
retrieved_ids:    [fin_q3, fin_q2, analyst_note, churn_report, fin_q1]
used_chunk_ids:   [fin_q3, fin_q2]         # numeric chunks only
utilisation_ratio: 0.40
```

The proxy correctly identifies that the analyst note and churn report
chunks (which contained the actual reasons for the revenue drop) were
not used. The agent ignored available evidence and supplemented with an
ungrounded macroeconomic claim.

This is the diagnostic that the **behavioral side can produce and the
retrieval side cannot**: ChunkScore sees what was retrieved, but only
the runtime trace shows which chunks contributed to the response.

## Method and limitations

`used_chunk_ids` is computed by **text-overlap proxy** — distinctive
tokens (words 4+ chars and numerics, minus stop words) are extracted
from each chunk's content and from the final response, and chunks
above a 0.30 overlap threshold (chunk-token side) are flagged as
"used".

Known limitations:

- **False positives on redundant retrieval sets.** When chunks are
  semantically near-duplicates (Scenario 1), the proxy cannot
  distinguish "used" from "incidentally overlaps."
- **False negatives on heavy paraphrase.** If the agent rewrites the
  retrieved content significantly, the proxy underestimates utilisation.
- **No semantic understanding.** Two chunks discussing the same topic in
  different vocabulary will appear unrelated to the proxy.
- **Order-insensitive.** The proxy does not distinguish "used early"
  from "used late" — only presence in the final response.

The output explicitly carries `utilisation_method: "text_overlap_proxy"`
so consumers know not to treat the signal as ground truth. Better
proxies (citation-marker tracking, attribution from intermediate
LLM steps) are possible extensions but each carries its own
fragility.

## Joint diagnostic with retrieval-side signals

When paired with retrieval-side `quality_density` and `coverage_diversity`
signals (as proposed in the forum spec), the four-dimensional matrix
becomes:

| quality_density | coverage_diversity | utilisation_ratio | Likely failure type |
|---|---|---|---|
| high | high | high | transformation problem (fix generation/reasoning) |
| high | high | **low** | **navigation problem (fix agent selection logic)** |
| high | low | — | retrieval composition problem (fix retrieval) |
| low | — | — | data problem (fix upstream) |

Scenario 2 in this PoC corresponds to the second row — high quality,
high diversity (analyst note and numeric chunks address different
facets), low utilisation (analyst note ignored). This is the
**navigation problem** that requires both systems to identify.

See `spec_v1_1_mapping.md` for the field-level mapping.
