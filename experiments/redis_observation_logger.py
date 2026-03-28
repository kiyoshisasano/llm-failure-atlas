"""
redis_observation_logger.py — Structured experiment runner for Redis demo

Records API responses as structured logs for offline analysis.
Does NOT perform anomaly detection, failure classification, or cause inference.

Usage:
  cd C:/Users/teiki/atlas-workspace/llm-failure-atlas
  python experiments/redis_observation_logger.py

Requires: Redis demo running at localhost:8000
"""

import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from adapters.redis_demo_adapter import RedisDemoAdapter

API_URL = "http://localhost:8000/api/help/chat"
adapter = RedisDemoAdapter()


def run_query(query: str, use_cache: bool = True) -> dict:
    """Execute a single query against Redis demo and return structured log."""
    payload = json.dumps({
        "message": query,
        "use_cache": use_cache,
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    ts = time.time()
    with urllib.request.urlopen(req) as resp:
        raw = json.loads(resp.read().decode("utf-8"))

    features = adapter.build_matcher_input(raw)
    gt = features.get("grounding", {})
    ca = features.get("cache", {})

    return {
        "query": query,
        "timestamp": round(ts, 3),
        "use_cache": use_cache,
        "tool_provided_data": gt.get("tool_provided_data"),
        "response_length": gt.get("response_length"),
        "source_data_length": gt.get("source_data_length"),
        "expansion_ratio": gt.get("expansion_ratio"),
        "uncertainty_acknowledged": gt.get("uncertainty_acknowledged"),
        "cache_hit": ca.get("hit"),
        "cache_similarity": ca.get("similarity"),
        "sources_count": len(raw.get("sources", [])),
        "blocked": raw.get("blocked", False),
        "response_time_ms": raw.get("response_time_ms"),
    }


def run_repeated(query: str, n: int, use_cache: bool = True) -> list:
    """Run the same query n times and collect logs."""
    results = []
    for i in range(n):
        r = run_query(query, use_cache=use_cache)
        results.append(r)
        status = "CACHE" if r["cache_hit"] else ("BLOCK" if r["blocked"] else "RAG")
        print(f"  [{i+1}/{n}] {status}  expansion={r['expansion_ratio']}  "
              f"sources={r['sources_count']}  time={r['response_time_ms']}ms")
    return results


def run_query_set(queries: list, n: int = 1, use_cache: bool = True) -> dict:
    """Run multiple queries, each n times."""
    all_results = {}
    for q in queries:
        print(f"\n  Query: \"{q}\"")
        all_results[q] = run_repeated(q, n, use_cache=use_cache)
    return all_results


def save_csv(results: list, path: str):
    """Save flat list of results to CSV."""
    if not results:
        print("  No results to save.")
        return
    keys = results[0].keys()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(results)
    print(f"  Saved {len(results)} rows to {path}")


# =====================================================================
# Preset experiments
# =====================================================================

def experiment_1_no_cache():
    """Same query repeated without cache — baseline variability."""
    print("\n=== Experiment 1: Same query, no cache (n=3) ===")
    results = run_repeated("Payment was declined", n=3, use_cache=False)
    save_csv(results, "experiment_1_no_cache.csv")
    return results


def experiment_2_with_cache():
    """Same query repeated with cache — expect 1 RAG + rest cache hits."""
    print("\n=== Experiment 2: Same query, with cache (n=3) ===")
    results = run_repeated("Payment was declined", n=3, use_cache=True)
    save_csv(results, "experiment_2_with_cache.csv")
    return results


def experiment_3_multi_query():
    """Multiple different queries — compare grounding across topics."""
    print("\n=== Experiment 3: Multiple queries (n=1 each) ===")
    queries = [
        "Payment was declined",
        "How do I cancel my subscription?",
        "Video keeps buffering",
        "How to set up parental controls?",
        "I forgot my password",
    ]
    all_results = run_query_set(queries, n=1, use_cache=False)
    flat = [r for results in all_results.values() for r in results]
    save_csv(flat, "experiment_3_multi_query.csv")
    return all_results


def experiment_4_cache_intent():
    """Seed with one query, then probe with a different but related query."""
    print("\n=== Experiment 4: Cache intent experiment ===")
    print("  Seeding cache...")
    seed = run_query("I want a refund for my subscription", use_cache=True)
    status = "CACHE" if seed["cache_hit"] else "RAG"
    print(f"  Seed: {status}")

    print("  Probing with different intent...")
    probes = run_repeated(
        "How do I cancel my subscription?", n=3, use_cache=True
    )
    all_results = [seed] + probes
    save_csv(all_results, "experiment_4_cache_intent.csv")
    return all_results


# =====================================================================
# Main
# =====================================================================

if __name__ == "__main__":
    # Quick connectivity check
    try:
        run_query("test", use_cache=False)
    except Exception as e:
        print(f"ERROR: Cannot reach Redis demo at {API_URL}")
        print(f"  {e}")
        print("  Make sure docker compose is running.")
        sys.exit(1)

    # Run small experiments (n=3 to minimize API cost)
    experiment_1_no_cache()
    experiment_2_with_cache()
    experiment_3_multi_query()
    experiment_4_cache_intent()

    print("\n=== All experiments complete ===")
    print("  CSV files saved in current directory.")