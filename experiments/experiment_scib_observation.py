"""
experiment_scib_observation.py — SCIB observation data collection

Collects cache_similarity distribution across diverse seed/probe pairs.
Does NOT change thresholds, signals, or matcher logic.

Usage:
  cd C:/Users/teiki/atlas-workspace/llm-failure-atlas
  python experiments/experiment_scib_observation.py

Requires: Redis demo running at localhost:8000 with help articles ingested.
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
CACHE_CLEAR_URL = "http://localhost:8000/api/cache/clear"
adapter = RedisDemoAdapter()


def clear_cache():
    """Clear semantic cache between experiment runs."""
    req = urllib.request.Request(CACHE_CLEAR_URL, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            resp.read()
        return True
    except Exception as e:
        print(f"  WARNING: cache clear failed: {e}")
        return False


def run_query(query: str, use_cache: bool = True) -> dict:
    """Execute query and return full observation record."""
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
        "use_cache": use_cache,
        "timestamp": round(ts, 3),
        "from_cache": raw.get("from_cache", False),
        "cache_similarity": raw.get("cache_similarity"),
        "sources_count": len(raw.get("sources", [])),
        "blocked": raw.get("blocked", False),
        "answer_preview": raw.get("answer", "")[:80],
        "response_length": gt.get("response_length"),
        "tool_provided_data": gt.get("tool_provided_data"),
        "expansion_ratio": gt.get("expansion_ratio"),
        "response_time_ms": raw.get("response_time_ms"),
    }


# =====================================================================
# Seed/Probe pairs — designed to test intent similarity boundaries
# =====================================================================

SEED_PROBE_PAIRS = [
    # Same topic, different intent
    {
        "label": "refund_vs_cancel",
        "seed": "I want a refund for my subscription",
        "probe": "How do I cancel my subscription?",
    },
    # Closely related but distinct actions
    {
        "label": "change_plan_vs_cancel",
        "seed": "How do I change my plan?",
        "probe": "How do I cancel my subscription?",
    },
    # Same domain, different problem
    {
        "label": "payment_declined_vs_unexpected_charge",
        "seed": "Payment was declined",
        "probe": "I see an unexpected charge on my account",
    },
    # Similar phrasing, different topic
    {
        "label": "password_vs_account_delete",
        "seed": "I forgot my password",
        "probe": "How do I delete my account?",
    },
    # Technical vs billing
    {
        "label": "buffering_vs_payment",
        "seed": "Video keeps buffering",
        "probe": "Payment was declined",
    },
    # Near-identical intent (should be valid cache hit)
    {
        "label": "password_reset_rephrase",
        "seed": "I forgot my password",
        "probe": "How do I reset my password?",
    },
    # Completely different topics
    {
        "label": "parental_controls_vs_buffering",
        "seed": "How to set up parental controls?",
        "probe": "Video keeps buffering",
    },
    # Subtle intent difference within same feature
    {
        "label": "download_vs_offline",
        "seed": "How to download for offline viewing?",
        "probe": "Can I watch movies without internet?",
    },
]


def run_seed_probe_pair(pair: dict) -> list:
    """Run one seed/probe experiment with cache on/off comparison."""
    label = pair["label"]
    seed_q = pair["seed"]
    probe_q = pair["probe"]
    results = []

    # Clear cache first
    clear_cache()
    time.sleep(0.5)

    # 1. Seed (cache on) — always RAG
    seed_result = run_query(seed_q, use_cache=True)
    seed_result["role"] = "seed"
    seed_result["pair_label"] = label
    results.append(seed_result)

    seed_status = "BLOCK" if seed_result["blocked"] else "RAG"
    print(f"    Seed:       {seed_status}  sources={seed_result['sources_count']}")

    # 2. Probe with cache on — may cache hit
    probe_cached = run_query(probe_q, use_cache=True)
    probe_cached["role"] = "probe_cache_on"
    probe_cached["pair_label"] = label
    results.append(probe_cached)

    probe_status = "CACHE" if probe_cached["from_cache"] else (
        "BLOCK" if probe_cached["blocked"] else "RAG"
    )
    sim = probe_cached["cache_similarity"]
    sim_str = f"{sim:.3f}" if sim is not None else "none"
    print(f"    Probe(on):  {probe_status}  similarity={sim_str}  "
          f"sources={probe_cached['sources_count']}")

    # 3. Probe with cache off — always RAG (comparison baseline)
    probe_nocache = run_query(probe_q, use_cache=False)
    probe_nocache["role"] = "probe_cache_off"
    probe_nocache["pair_label"] = label
    results.append(probe_nocache)

    probe_nc_status = "BLOCK" if probe_nocache["blocked"] else "RAG"
    print(f"    Probe(off): {probe_nc_status}  sources={probe_nocache['sources_count']}  "
          f"expansion={probe_nocache['expansion_ratio']}")

    return results


def save_csv(results: list, path: str):
    """Save results to CSV."""
    if not results:
        return
    keys = results[0].keys()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(results)
    print(f"\n  Saved {len(results)} rows to {path}")


def main():
    # Connectivity check
    try:
        clear_cache()
    except Exception as e:
        print(f"ERROR: Cannot reach Redis demo: {e}")
        sys.exit(1)

    print("=" * 58)
    print("  SCIB Observation Data Collection")
    print("  " + str(len(SEED_PROBE_PAIRS)) + " seed/probe pairs")
    print("=" * 58)

    all_results = []

    for pair in SEED_PROBE_PAIRS:
        print(f"\n  [{pair['label']}]")
        print(f"    Seed:  \"{pair['seed']}\"")
        print(f"    Probe: \"{pair['probe']}\"")

        results = run_seed_probe_pair(pair)
        all_results.extend(results)

    save_csv(all_results, "scib_observation_data.csv")

    # Summary
    print("\n" + "=" * 58)
    print("  SUMMARY")
    print("=" * 58)

    cache_hits = [r for r in all_results if r["role"] == "probe_cache_on" and r["from_cache"]]
    cache_misses = [r for r in all_results if r["role"] == "probe_cache_on" and not r["from_cache"]]
    blocked = [r for r in all_results if r["blocked"]]

    print(f"  Total records:     {len(all_results)}")
    print(f"  Cache hits (probe): {len(cache_hits)}")
    print(f"  Cache misses:      {len(cache_misses)}")
    print(f"  Blocked:           {len(blocked)}")

    if cache_hits:
        sims = [r["cache_similarity"] for r in cache_hits if r["cache_similarity"] is not None]
        if sims:
            print(f"  Similarity range:  {min(sims):.3f} - {max(sims):.3f}")
            print(f"  Similarity values: {', '.join(f'{s:.3f}' for s in sims)}")

    print()


if __name__ == "__main__":
    main()