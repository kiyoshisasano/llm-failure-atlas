"""
analyze_cache_divergence.py — Analyze cache reuse patterns from SCIB observation data

Reads scib_observation_data.csv and computes per-pair hash comparisons.
No anomaly detection, no threshold changes, no matcher modifications.

Usage:
  cd C:/Users/teiki/atlas-workspace/llm-failure-atlas
  python experiments/analyze_cache_divergence.py
"""

import csv
import sys
from collections import defaultdict


def load_csv(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def analyze(rows: list):
    # Group by pair_label
    pairs = defaultdict(dict)
    for row in rows:
        label = row.get("pair_label", "")
        role = row.get("role", "")
        if label and role:
            pairs[label][role] = row

    print("=" * 72)
    print("  Cache Divergence Analysis")
    print("=" * 72)

    # Per-pair analysis
    results = []
    for label, roles in sorted(pairs.items()):
        seed = roles.get("seed")
        probe_on = roles.get("probe_cache_on")
        probe_off = roles.get("probe_cache_off")

        if not (seed and probe_on and probe_off):
            continue

        seed_hash = seed.get("answer_hash", "")
        on_hash = probe_on.get("answer_hash", "")
        off_hash = probe_off.get("answer_hash", "")
        from_cache = probe_on.get("from_cache", "").lower() == "true"
        similarity = probe_on.get("cache_similarity", "")
        blocked = probe_on.get("blocked", "").lower() == "true"

        cache_reused = on_hash == seed_hash
        diverges = on_hash != off_hash

        results.append({
            "label": label,
            "from_cache": from_cache,
            "blocked": blocked,
            "similarity": similarity,
            "cache_reused": cache_reused,
            "diverges_from_fresh": diverges,
            "seed_hash": seed_hash[:8],
            "on_hash": on_hash[:8],
            "off_hash": off_hash[:8],
        })

    # Table
    print()
    header = (
        f"  {'pair_label':<40} {'hit':>5} {'reused':>7} "
        f"{'diverges':>9} {'similarity':>11}"
    )
    print(header)
    print("  " + "-" * 70)

    for r in results:
        if r["blocked"]:
            hit_str = "BLOCK"
        elif r["from_cache"]:
            hit_str = "YES"
        else:
            hit_str = "no"

        reused_str = "YES" if r["cache_reused"] else "no"
        div_str = "YES" if r["diverges_from_fresh"] else "no"
        sim_str = f"{float(r['similarity']):.3f}" if r["similarity"] else "-"

        print(
            f"  {r['label']:<40} {hit_str:>5} {reused_str:>7} "
            f"{div_str:>9} {sim_str:>11}"
        )

    # Hash detail for cache hits
    cache_hit_results = [r for r in results if r["from_cache"]]
    if cache_hit_results:
        print()
        print("  Hash detail (cache hit pairs):")
        for r in cache_hit_results:
            print(f"    {r['label']}:")
            print(f"      seed={r['seed_hash']}  on={r['on_hash']}  "
                  f"off={r['off_hash']}")
            match = "SAME" if r["cache_reused"] else "DIFFERENT"
            print(f"      on vs seed: {match}")

    # Summary
    print()
    print("  " + "-" * 70)
    total = len(results)
    hits = len(cache_hit_results)
    reused = sum(1 for r in cache_hit_results if r["cache_reused"])
    diverges = sum(1 for r in cache_hit_results if r["diverges_from_fresh"])
    blocked_count = sum(1 for r in results if r["blocked"])

    print(f"  Total pairs:              {total}")
    print(f"  Cache hits:               {hits}")
    print(f"  Cache reused (= seed):    {reused}")
    print(f"  Diverges from fresh RAG:  {diverges}")
    print(f"  Blocked by guardrail:     {blocked_count}")
    print()


if __name__ == "__main__":
    path = "scib_observation_data.csv"
    if len(sys.argv) > 1:
        path = sys.argv[1]

    try:
        rows = load_csv(path)
    except FileNotFoundError:
        print(f"ERROR: {path} not found.")
        print("  Run experiment_scib_observation.py first.")
        sys.exit(1)

    analyze(rows)