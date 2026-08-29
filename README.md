# litellm-gateway-demo

A runnable demo of **LiteLLM + Redis + Prometheus + Grafana**, with a KEDA ScaledObject for Kubernetes autoscaling.

Built to demonstrate: LLM cost observability, team-based routing, and event-driven autoscaling — all wired together.

## Architecture

```
  Load Generator (Python)
  ─ team_a: 70% of traffic  ──┐
  ─ team_b: 30% of traffic  ──┤
                              │  POST /v1/chat/completions
                              │  model: gpt-3.5-turbo | gpt-4 | claude-haiku
                              │  user: team_a (for per-team usage tracking)
                              ▼
                    ┌─────────────────────┐
                    │    LiteLLM Proxy    │  :4000
                    │                    │
                    │  model routing      │  gpt-3.5-turbo → mock / real OpenAI
                    │  per-model RPM/TPM  │  gpt-4         → mock / real OpenAI
                    │  cost tracking      │  claude-haiku  → mock / real Anthropic
                    │  /metrics endpoint  │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │ rate limit      │                │ scrape /metrics
              ▼ state          │                ▼
   ┌─────────────────┐         │     ┌─────────────────────┐
   │      Redis      │  :6379  │     │     Prometheus      │  :9090
   │                 │         │     │                     │
   │ - RPM counters  │         │     │  requests_total     │
   │ - request queue │         │     │  latency histogram  │
   └────────┬────────┘         │     │  tokens_total       │
            │                  │     └──────────┬──────────┘
            │ KEDA watches     │                │ query
            │ queue depth      │                ▼
            │ (k8s only)       │     ┌─────────────────────┐
            ▼                  │     │       Grafana        │  :3000
   ┌─────────────────┐         │     │                     │
   │  KEDA           │         │     │  req/sec by model   │
   │  ScaledObject   │         │     │  p50/p99 latency    │
   │                 │         │     │  tokens/min         │
   │  min: 1 pod     │         │     │  error rate         │
   │  max: 10 pods   │         │     │  (auto-provisioned) │
   └─────────────────┘         │     └─────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │  No real API keys   │
                    │  needed — mock_     │
                    │  response mode by   │
                    │  default            │
                    └─────────────────────┘
```

## What it demonstrates

| Concept | How it shows up |
|---|---|
| **LLM cost observability** | Token counters per model in Prometheus/Grafana |
| **Model routing** | Three models available; load generator distributes across them |
| **Per-model rate limiting** | RPM/TPM caps in litellm_config.yaml — LiteLLM enforces via Redis |
| **Fallback routing** | `gpt-4` falls back to `gpt-3.5-turbo` on error (config) |
| **Event-driven autoscaling** | KEDA ScaledObject watches Redis queue + Prometheus request rate |
| **End-to-end observability** | Load generator → LiteLLM → Prometheus → Grafana dashboard |

## Quickstart

```bash
# 1. Clone and start everything (no API keys needed — uses mock responses)
git clone https://github.com/ShivSingh96/llm-gateway-demo
cd llm-gateway-demo
docker compose up -d

# 2. Wait ~15s for all services to start
docker compose ps

# 3. Run the load generator (simulates team_a + team_b traffic)
python3 load_generator/generate.py --rps 3 --duration 120

# 4. Open Grafana — dashboard auto-loads
open http://localhost:3000

# 5. Open Prometheus (optional — raw metrics)
open http://localhost:9090
```

**Grafana credentials:** anonymous access enabled — no login required.

## Test a single request manually

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-demo-master-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "What is Kubernetes?"}],
    "user": "team_a"
  }' | python3 -m json.tool
```

## Switch to real API keys

By default every model uses `mock_response` — no real API calls are made.

To use real models:

```bash
# 1. Copy the env template
cp .env.example .env

# 2. Fill in your keys
vi .env

# 3. Edit config/litellm_config.yaml:
#    Remove the `mock_response:` line from each model entry
#    Change api_key: fake-key to api_key: os.environ/OPENAI_API_KEY

# 4. Restart LiteLLM
docker compose restart litellm
```

## Discover actual Prometheus metric names

LiteLLM metric names can change between releases. Discover what your version exposes:

```bash
curl -s http://localhost:4000/metrics | grep "^# HELP"
```

Adjust the Grafana dashboard PromQL if the names differ from `litellm_requests_total`.

## KEDA autoscaling (Kubernetes)

The `keda/scaledobject.yaml` scales LiteLLM Deployment pods based on two triggers:

1. **Redis list length** — when async request queue depth > 10 items
2. **Prometheus query** — when `sum(rate(litellm_requests_total[1m])) > 50` req/sec

```bash
# Assumes LiteLLM deployed as a Deployment in namespace llm-gateway
# and KEDA is installed on the cluster

kubectl create namespace llm-gateway
kubectl apply -f keda/scaledobject.yaml

# Watch pods scale under load
kubectl get hpa -n llm-gateway -w
kubectl get pods -n llm-gateway -w
```

See `keda/scaledobject.yaml` for full annotations explaining each field.

## Project structure

```
llm-gateway-demo/
├── .github/
│   └── workflows/
│       └── validate.yml          # compose syntax, ruff lint, yaml check
├── config/
│   ├── litellm_config.yaml       # model routing, rate limits, callbacks
│   ├── prometheus.yml            # scrape config
│   ├── grafana-datasource.yml    # auto-provision Prometheus datasource
│   └── grafana-dashboard-provider.yml
├── dashboards/
│   └── llm-gateway.json          # auto-provisioned Grafana dashboard
├── keda/
│   └── scaledobject.yaml         # event-driven autoscaling for Kubernetes
├── load_generator/
│   └── generate.py               # Python stdlib only, no pip deps
├── .env.example
├── docker-compose.yml
└── README.md
```
