#!/usr/bin/env python3
"""
quickstart_demo.py — Run the full pipeline in under 1 minute.

This script demonstrates the complete flow:
  1. Adapt a raw LangChain trace into matcher input
  2. Run the matcher to detect failures
  3. Run the debugger pipeline to diagnose root cause and generate fixes

No external dependencies beyond pyyaml.

Usage:
  python quickstart_demo.py
"""

import json
import sys
from pathlib import Path

# Ensure imports work from both repos
DEBUGGER_ROOT = Path(__file__).parent
ATLAS_ROOT = DEBUGGER_ROOT.parent / "llm-failure-atlas"
sys.path.insert(0, str(ATLAS_ROOT))
sys.path.insert(0, str(ATLAS_ROOT / "adapters"))
sys.path.insert(0, str(DEBUGGER_ROOT))

SAMPLE_TRACE = ATLAS_ROOT / "adapters" / "sample_langchain_trace.json"
FAILURES_DIR = ATLAS_ROOT / "failures"


def step(n, title):
    print(f"\n{'='*50}")
    print(f"  Step {n}: {title}")
    print(f"{'='*50}\n")


def main():
    print("\n🚀 LLM Failure Atlas — Quickstart Demo\n")

    # ---- Step 1: Load raw trace ----
    step(1, "Load raw agent trace")

    if not SAMPLE_TRACE.exists():
        print(f"❌ Sample trace not found: {SAMPLE_TRACE}")
        print("   Make sure you're running from the llm-failure-atlas root.")
        sys.exit(1)

    with open(SAMPLE_TRACE, encoding="utf-8") as f:
        raw_trace = json.load(f)

    print(f"  Source: {SAMPLE_TRACE.name}")
    print(f"  Query:  {raw_trace['inputs']['query']}")
    print(f"  Output: {raw_trace['outputs']['response']}")
    print(f"  Steps:  {len(raw_trace['steps'])}")

    # ---- Step 2: Adapt to matcher format ----
    step(2, "Adapt trace → matcher input")

    from langchain_adapter import LangChainAdapter
    adapter = LangChainAdapter()
    matcher_input = adapter.build_matcher_input(raw_trace)

    print(f"  Cache hit:      {matcher_input['cache']['hit']}")
    print(f"  Intent match:   {matcher_input['cache']['query_intent_similarity']}")
    print(f"  Tool calls:     {matcher_input['tools']['call_count']}")
    print(f"  Tool repeats:   {matcher_input['tools']['repeat_count']}")
    print(f"  Alignment:      {matcher_input['response']['alignment_score']}")
    print(f"  User corrected: {matcher_input['interaction']['user_correction_detected']}")

    # ---- Step 3: Run matcher ----
    step(3, "Run matcher → detect failures")

    from matcher import run as run_matcher

    # Save adapted log for matcher
    import tempfile
    tmp_log = Path(tempfile.gettempdir()) / "quickstart_adapted.json"
    with open(tmp_log, "w", encoding="utf-8") as f:
        json.dump(matcher_input, f)

    diagnosed = []
    for pattern_file in sorted(FAILURES_DIR.glob("*.yaml")):
        result = run_matcher(str(pattern_file), str(tmp_log))
        if result.get("diagnosed"):
            diagnosed.append(result)
            print(f"  ✅ {result['failure_id']:40s} confidence={result['confidence']}")
        else:
            print(f"     {result['failure_id']:40s} (not detected)")

    print(f"\n  Total diagnosed: {len(diagnosed)} failures")

    if not diagnosed:
        print("\n  No failures detected. Demo complete.")
        return

    # ---- Step 4: Run debugger pipeline ----
    step(4, "Run debugger → diagnose root cause")

    if not DEBUGGER_ROOT.exists():
        print(f"  ⚠ agent-failure-debugger not found at {DEBUGGER_ROOT}")
        print("  Skipping pipeline. Clone it as a sibling directory to run full diagnosis.")
        print("\n  Matcher output (use as debugger input):")
        print(json.dumps(diagnosed, indent=2))
        return

    from pipeline import run_pipeline

    result = run_pipeline(diagnosed, use_learning=True, top_k=1)
    s = result["summary"]

    print(f"  Root cause:  {s['root_cause']}")
    print(f"  Confidence:  {s['root_confidence']}")
    print(f"  Failures:    {s['failure_count']}")
    print(f"  Fixes:       {s['fix_count']}")
    print(f"  Gate:        {s['gate_mode']} (score: {s['gate_score']})")

    # ---- Summary ----
    print(f"\n{'='*50}")
    print(f"  Demo Complete!")
    print(f"{'='*50}")
    print(f"""
  What happened:
    1. Raw LangChain trace was adapted to matcher format
    2. Matcher detected {len(diagnosed)} failure(s) from 15 patterns
    3. Debugger identified '{s['root_cause']}' as root cause
    4. Generated {s['fix_count']} fix(es) with gate mode: {s['gate_mode']}

  Next steps:
    - Try with your own agent logs
    - Write a custom adapter (see adapters/base_adapter.py)
    - Use the API: from pipeline import run_pipeline
""")


if __name__ == "__main__":
    main()