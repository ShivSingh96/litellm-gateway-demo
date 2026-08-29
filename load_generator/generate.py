#!/usr/bin/env python3
"""
Load generator for llm-gateway-demo.

Simulates two teams sending traffic to LiteLLM proxy:
  team_a: 70% of requests, prefers gpt-3.5-turbo
  team_b: 30% of requests, prefers claude-haiku

No external dependencies — pure Python stdlib.

Usage:
    python3 load_generator/generate.py
    python3 load_generator/generate.py --rps 5 --duration 120
    python3 load_generator/generate.py --rps 10 --workers 10 --url http://localhost:4000
"""

import argparse
import concurrent.futures
import json
import random
import statistics
import time
import urllib.request
import urllib.error

PROMPTS = [
    "Summarize the benefits of microservices architecture in two sentences.",
    "What is Kubernetes and why do platform teams use it?",
    "Explain GitOps in three sentences.",
    "What is a service mesh and when would you use one?",
    "Describe the difference between horizontal and vertical scaling.",
    "What problem does a schema registry solve?",
    "When would you choose Kafka over RabbitMQ?",
    "What is the purpose of a sidecar container in Kubernetes?",
]

TEAMS = {
    "team_a": {
        "models": ["gpt-3.5-turbo", "gpt-3.5-turbo", "gpt-4"],  # weighted via repetition
        "weight": 0.7,
    },
    "team_b": {
        "models": ["claude-haiku"],
        "weight": 0.3,
    },
}


def _pick_team() -> tuple[str, dict]:
    names = list(TEAMS.keys())
    weights = [TEAMS[n]["weight"] for n in names]
    chosen = random.choices(names, weights=weights, k=1)[0]
    return chosen, TEAMS[chosen]


def _send(url: str, master_key: str, team_name: str, team: dict) -> dict:
    model = random.choice(team["models"])
    prompt = random.choice(PROMPTS)

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "user": team_name,              # LiteLLM uses this for per-user tracking in metrics
        "metadata": {"team": team_name},
    }).encode()

    req = urllib.request.Request(
        f"{url}/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {master_key}",
        },
    )

    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            latency = time.monotonic() - start
            data = json.loads(resp.read())
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return {"team": team_name, "model": model, "status": "ok",
                    "latency": latency, "tokens": tokens}
    except urllib.error.HTTPError as e:
        latency = time.monotonic() - start
        return {"team": team_name, "model": model, "status": f"http_{e.code}",
                "latency": latency, "tokens": 0}
    except Exception as e:
        latency = time.monotonic() - start
        return {"team": team_name, "model": model, "status": "error",
                "latency": latency, "tokens": 0, "error": str(e)[:80]}


def _print_summary(results: list[dict]) -> None:
    ok = [r for r in results if r["status"] == "ok"]
    errors = [r for r in results if r["status"] != "ok"]

    print("\n" + "─" * 60)
    print(f"  Total    : {len(results)}")
    print(f"  OK       : {len(ok)}")
    print(f"  Errors   : {len(errors)}")

    if ok:
        latencies_ms = sorted(r["latency"] * 1000 for r in ok)
        p50 = statistics.median(latencies_ms)
        p99 = latencies_ms[max(0, int(len(latencies_ms) * 0.99) - 1)]
        total_tokens = sum(r["tokens"] for r in ok)
        print(f"  p50      : {p50:.0f}ms")
        print(f"  p99      : {p99:.0f}ms")
        print(f"  tokens   : {total_tokens:,}")

    # per-team breakdown
    for team in TEAMS:
        team_results = [r for r in results if r["team"] == team]
        team_ok = [r for r in team_results if r["status"] == "ok"]
        if team_results:
            pct = len(team_ok) / len(team_results) * 100
            print(f"  {team:8s} : {len(team_ok)}/{len(team_results)} ok ({pct:.0f}%)")

    # per-model breakdown
    models_seen = sorted({r["model"] for r in results})
    print()
    for model in models_seen:
        model_results = [r for r in results if r["model"] == model]
        model_ok = [r for r in model_results if r["status"] == "ok"]
        avg_ms = statistics.mean(r["latency"] * 1000 for r in model_ok) if model_ok else 0
        print(f"  {model:25s} {len(model_ok):4d} ok  avg {avg_ms:.0f}ms")

    if errors:
        print()
        for r in errors[:5]:
            print(f"  ERR {r['team']:8s} {r['model']:20s} {r['status']}  {r.get('error', '')}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="LiteLLM load generator")
    parser.add_argument("--url",      default="http://localhost:4000", help="LiteLLM base URL")
    parser.add_argument("--key",      default="sk-demo-master-key",    help="master API key")
    parser.add_argument("--rps",      type=float, default=2.0,         help="requests per second")
    parser.add_argument("--duration", type=int,   default=60,          help="run duration in seconds")
    parser.add_argument("--workers",  type=int,   default=5,           help="concurrent HTTP workers")
    args = parser.parse_args()

    interval = 1.0 / args.rps
    deadline = time.monotonic() + args.duration
    results: list[dict] = []

    print(f"LiteLLM load generator")
    print(f"  target  : {args.url}")
    print(f"  rate    : {args.rps} req/s for {args.duration}s")
    print(f"  workers : {args.workers}")
    print("─" * 60)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures: list[concurrent.futures.Future] = []

        while time.monotonic() < deadline:
            team_name, team_config = _pick_team()
            futures.append(pool.submit(_send, args.url, args.key, team_name, team_config))
            time.sleep(interval)

        for f in concurrent.futures.as_completed(futures):
            r = f.result()
            results.append(r)
            icon = "✓" if r["status"] == "ok" else "✗"
            tok = f"{r['tokens']:4d}tok" if r["tokens"] else "      "
            print(f"  {icon} {r['team']:8s}  {r['model']:22s}  {r['latency']*1000:6.0f}ms  {tok}")

    _print_summary(results)


if __name__ == "__main__":
    main()
