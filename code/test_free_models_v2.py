#!/usr/bin/env python3
"""Test free coding models via OpenRouter."""
import requests, time, json, os

API_KEY = None
with open(os.path.expanduser('/root/.hermes/.env')) as f:
    for line in f:
        if line.startswith('OPENROUTER_API_KEY='):
            API_KEY = line.strip().split('=', 1)[1]
            break

URL = "https://openrouter.ai/api/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
PROMPT = "Write a Python function to find the longest palindromic substring. Include type hints and docstring. Return ONLY the code, no explanation."

# Free models on OpenRouter
MODELS = [
    "nvidia/nemotron-3.5-lightning:free",
    "thinkingmachines/inkling:free",
    "thinkingmachines/inkling-small:free",
    "poolside/laguna-xs-2.1:free",
    "poolside/laguna-s-2.1:free",
    "z-ai/glm-5.2:free",
    "cohere/north-mini-code:free",
]

results = []
for model in MODELS:
    print(f"\n--- {model} ---")
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
                has_code = "def " in text
                quality = 5 if has_code and len(text) > 100 else (3 if has_code else 1)
                times.append(dt)
                successes += 1
                quality_sum += quality
                print(f"  Run {run+1}: {dt:.1f}s, q={quality}, len={len(text)}")
            else:
                err = r.text[:80]
                print(f"  Run {run+1}: HTTP {r.status_code} — {err}")
        except Exception as e:
            print(f"  Run {run+1}: ERROR — {e}")
    
    avg_t = sum(times)/len(times) if times else 0
    avg_q = quality_sum/successes if successes else 0
    results.append({"model": model, "ok": f"{successes}/3", "time": f"{avg_t:.1f}s", "q": f"{avg_q:.1f}"})

print("\n\n=== RESULTS ===")
for r in results:
    print(f"{r['model']:<45} ok={r['ok']}  time={r['time']}  quality={r['q']}")
