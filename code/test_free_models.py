#!/usr/bin/env python3
"""Test free coding models via OpenRouter."""
import requests, time, json, os

API_KEY = None
with open(os.path.expanduser('/root/.hermes/.env')) as f:
    for line in f:
        if line.startswith('OPENROUTER_API_KEY='):
            API_KEY = line.strip().split('=', 1)[1]
            break

if not API_KEY:
    print("ERROR: OPENROUTER_API_KEY not found")
    exit(1)

URL = "https://openrouter.ai/api/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
PROMPT = "Write a Python function to find the longest palindromic substring. Include type hints and docstring. Return ONLY the code, no explanation."

# Free models on OpenRouter
MODELS = [
    "deepseek/deepseek-chat-v3-0324:free",
    "qwen/qwen3-235b-a22b:free",
    "meta-llama/llama-4-maverick:free",
    "google/gemma-3-27b-it:free",
    "mistralai/devstral-small:free",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1:free",
]

results = []
for model in MODELS:
    print(f"\n--- Testing: {model} ---")
    times = []
    successes = 0
    quality_sum = 0
    
    for run in range(3):
        t0 = time.time()
        try:
            r = requests.post(URL, json={
                "model": model,
                "messages": [{"role": "user", "content": PROMPT}],
                "max_tokens": 500,
                "temperature": 0.3,
            }, headers=HEADERS, timeout=30)
            dt = time.time() - t0
            
            if r.status_code == 200:
                d = r.json()
                text = d.get("choices", [{}])[0].get("message", {}).get("content", "")
                # Check if response contains code
                has_code = "def " in text and "def " in text
                quality = 5 if has_code and len(text) > 100 else (3 if has_code else 1)
                times.append(dt)
                successes += 1
                quality_sum += quality
                print(f"  Run {run+1}: {dt:.1f}s, quality={quality}, len={len(text)}")
            else:
                print(f"  Run {run+1}: HTTP {r.status_code} — {r.text[:100]}")
        except Exception as e:
            print(f"  Run {run+1}: ERROR — {e}")
    
    avg_time = sum(times) / len(times) if times else 0
    avg_quality = quality_sum / successes if successes else 0
    results.append({
        "model": model,
        "success_rate": f"{successes}/3",
        "avg_time": f"{avg_time:.1f}s",
        "quality": f"{avg_quality:.1f}/5",
    })

print("\n\n=== FINAL RESULTS ===")
print(f"{'Model':<55} {'Success':>8} {'Avg Time':>10} {'Quality':>10}")
print("-" * 85)
for r in results:
    print(f"{r['model']:<55} {r['success_rate']:>8} {r['avg_time']:>10} {r['quality']:>10}")
