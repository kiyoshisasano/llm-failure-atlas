# experiment_scib_round2.py
# Additional 12 seed/probe pairs for SCIB data collection.
# Run from atlas-workspace/
#
# Usage: python experiment_scib_round2.py

import requests
import hashlib
import json
import time
import csv
import os
from datetime import datetime

API_URL = "http://localhost:8000/api/help/chat"
CACHE_CLEAR_URL = "http://localhost:8000/api/cache/clear"

PAIRS = [
    # Different intent pairs (expect SCIB if cache hits)
    ("How do I upgrade my plan?", "How do I downgrade my plan?"),
    ("I want to cancel my account", "I want to delete my account"),
    ("How do I change my email?", "How do I change my password?"),
    ("My video keeps buffering", "My video has no sound"),
    ("How do I get a refund?", "How do I check my billing history?"),
    ("I can't log in to my account", "I forgot my username"),
    
    # Close rephrase pairs (expect valid cache reuse)
    ("How do I update my payment method?", "How can I change my credit card?"),
    ("I need help with my subscription", "I have a subscription question"),
    ("How do I contact support?", "How can I reach customer service?"),
    ("My account was locked", "My account is locked out"),
    
    # Edge cases (same domain, subtle difference)
    ("How do I add a profile?", "How do I remove a profile?"),
    ("I was charged twice", "I was charged the wrong amount"),
]


def query(message, use_cache=True):
    try:
        resp = requests.post(API_URL, json={
            "message": message,
            "use_cache": use_cache,
        }, timeout=30)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def clear_cache():
    try:
        requests.post(CACHE_CLEAR_URL, timeout=10)
        time.sleep(0.5)
    except Exception:
        pass


def answer_hash(response):
    answer = response.get("answer", "")
    return hashlib.md5(answer.encode()).hexdigest()[:8]


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = f"scib_round2_{timestamp}.csv"
    
    results = []
    
    print(f"Running {len(PAIRS)} seed/probe pairs...")
    print()
    
    for i, (seed_q, probe_q) in enumerate(PAIRS):
        print(f"Pair {i+1}/{len(PAIRS)}: {seed_q[:40]}... vs {probe_q[:40]}...")
        
        # Clear cache
        clear_cache()
        
        # Step 1: Seed (cache on)
        seed_resp = query(seed_q, use_cache=True)
        if "error" in seed_resp:
            print(f"  ERROR: {seed_resp['error']}")
            continue
        seed_hash = answer_hash(seed_resp)
        time.sleep(1)
        
        # Step 2: Probe (cache on)
        probe_resp = query(probe_q, use_cache=True)
        if "error" in probe_resp:
            print(f"  ERROR: {probe_resp['error']}")
            continue
        probe_hash = answer_hash(probe_resp)
        probe_cached = probe_resp.get("from_cache", False)
        probe_similarity = probe_resp.get("cache_similarity")
        time.sleep(1)
        
        # Step 3: Probe (cache off) — baseline
        probe_fresh = query(probe_q, use_cache=False)
        if "error" in probe_fresh:
            fresh_hash = "error"
        else:
            fresh_hash = answer_hash(probe_fresh)
        
        # Determine if seed answer was reused
        reused = (probe_hash == seed_hash) if probe_cached else False
        
        row = {
            "pair_index": i + 1,
            "seed_query": seed_q,
            "probe_query": probe_q,
            "cache_hit": probe_cached,
            "similarity": probe_similarity if probe_similarity else "",
            "seed_hash": seed_hash,
            "probe_hash": probe_hash,
            "fresh_hash": fresh_hash,
            "reused_seed": reused,
            "blocked": probe_resp.get("blocked", False),
        }
        results.append(row)
        
        status = "CACHE HIT" if probe_cached else "cache miss"
        if probe_resp.get("blocked"):
            status = "BLOCKED"
        sim_str = f" sim={probe_similarity:.3f}" if probe_similarity else ""
        reuse_str = f" REUSED={reused}" if probe_cached else ""
        print(f"  {status}{sim_str}{reuse_str}")
        
        time.sleep(1)
    
    # Write CSV
    if results:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print(f"\nResults saved to {csv_path}")
    
    # Summary
    print("\n=== Summary ===")
    cache_hits = [r for r in results if r["cache_hit"]]
    reused = [r for r in results if r["reused_seed"]]
    blocked = [r for r in results if r["blocked"]]
    print(f"Total pairs: {len(results)}")
    print(f"Cache hits: {len(cache_hits)}")
    print(f"Seed answer reused: {len(reused)}")
    print(f"Blocked: {len(blocked)}")
    
    if cache_hits:
        print("\nCache hit details:")
        for r in cache_hits:
            print(f"  {r['seed_query'][:30]}... -> {r['probe_query'][:30]}...")
            print(f"    similarity={r['similarity']}  reused={r['reused_seed']}  hashes: seed={r['seed_hash']} probe={r['probe_hash']} fresh={r['fresh_hash']}")


if __name__ == "__main__":
    main()