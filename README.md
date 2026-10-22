# Kubernetes Observability Platform

A production-shaped observability stack for Kubernetes, built on
**kube-prometheus-stack** and extended the way a platform/SRE team actually
runs it: SLO-based alerting with multi-window burn rates, hand-written
Grafana dashboards provisioned as code, a log pipeline that joins metrics
by label, and a demo workload you can break on purpose to watch it all fire.

Nothing here is a screenshot-driven tutorial — every component is a file in
this repo, rendered and validated (Helm lint/template, `promtool`, `pytest`)
before it was committed.

## Architecture

```
                        ┌──────────────────── cluster ────────────────────┐
                        │                                                   │
  workloads ──/metrics──►  ServiceMonitors ──►  Prometheus ──► recording ──┐ │
  (demo-app)                (label-selected)      │   rules (SLI)         │ │
                                                  │                       ▼ │
  kube-state-metrics ─────►  object metrics ──────┤              SLO burn-  │
  node-exporter ──────────►  node metrics ────────┤              rate alerts│
                                                  │                  │    │
                                                  ▼                  ▼    │
  pod logs ─► Promtail ─► Loki ─► Grafana ◄── dashboards (ConfigMap       │
                                   ▲        sidecar) + platform alerts    │
                                   │                  │                   │
                                   └──── datasources ─┴─► Alertmanager ───┘ │
                                                  │                         │
                        └─────────────────────────┼─────────────────────────┘
                                                  ▼
                                     Slack #alerts-critical / #alerts-warnings
```

| Layer | Choice | Why |
|---|---|---|
| Metrics & alerting | kube-prometheus-stack 92.11.0 (Prometheus Operator) | The de-facto standard; CRD-driven scrape/rule management |
| Dashboards | Grafana sidecar-provisioned ConfigMaps | Dashboards live in git, reviewed like code, never click-ops |
| Logs | Loki (SingleBinary) + Promtail | Same label model as Prometheus — labels are the join key |
| Alert routing | Alertmanager → Slack, by severity | Webhook from a Secret file, never from git |
| SLOs | Recording rules + multi-window burn-rate alerts | Alert on user impact, not on symptoms |

## Repository layout

```
.
├── helm/
│   └── values-kube-prometheus-stack.yaml   # sizing, retention, routing, sidecars
├── alerts/
│   ├── slo-recording-rules.yaml            # SLI time series (error ratio per window)
│   ├── slo-burn-rate-alerts.yaml           # 4 multi-window burn-rate alerts
│   └── platform-alerts.yaml                # workloads / nodes / storage / self-monitoring
├── dashboards/                             # dashboard JSON — source of truth
│   ├── golden-signals.json
│   └── slo-error-budget.json
├── manifests/dashboards/                   # generated ConfigMaps (scripts/sync-*.py)
├── logs/                                   # Loki + Promtail values, Loki datasource, explainer
├── demo/                                   # demo-app: Python /metrics workload + manifests
├── runbooks/                               # linked from the alerts themselves
└── scripts/sync-dashboard-configmaps.py    # dashboards/*.json -> ConfigMaps
```

## Install

Prerequisites: a Kubernetes cluster (kind, minikube, EKS, AKS — anything
recent), `kubectl`, Helm 3.

### 1. Secrets first (nothing sensitive is in git)

```bash
kubectl create namespace monitoring

# Grafana admin credentials
kubectl -n monitoring create secret generic grafana-admin-credentials \
  --from-literal=admin-user=admin \
  --from-literal=admin-password='<choose-a-strong-one>'

# Slack webhook for Alertmanager (read from a mounted file at runtime)
kubectl -n monitoring create secret generic alertmanager-slack-webhook \
  --from-literal=webhook-url='https://hooks.slack.com/services/XXX/YYY/ZZZ'
```

### 2. The stack

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --version 92.11.0 -n monitoring \
  -f helm/values-kube-prometheus-stack.yaml
```

Key settings in the values file: 15d / 50Gi Prometheus retention with WAL
compression, cluster-wide ServiceMonitor/PrometheusRule discovery (still
gated by the `release: monitoring` label), Grafana persistence, and the
dashboard/datasource sidecars.

### 3. Dashboards and rules from this repo

```bash
kubectl apply -f manifests/dashboards/     # Golden Signals + SLO dashboards
kubectl apply -f alerts/                   # recording rules, burn alerts, platform alerts
```

### 4. The demo workload

```bash
docker build -t demo-app:0.1.0 demo/app
kind load docker-image demo-app:0.1.0        # or: minikube image load demo-app:0.1.0
kubectl apply -f demo/manifests/
kubectl -n demo port-forward svc/demo-app 8080:8080
curl localhost:8080/work                     # business endpoint (2% errors by default)
```

The workload's ServiceMonitor stamps `job="demo-app"` on its metrics —
the identity every recording rule, alert and dashboard in this repo keys on.

### 5. Logs (optional, recommended)

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm upgrade --install loki     grafana/loki     --version 7.3.0  -n monitoring -f logs/values-loki.yaml
helm upgrade --install promtail grafana/promtail --version 6.17.1 -n monitoring -f logs/values-promtail.yaml
kubectl apply -f logs/grafana-datasource-loki.yaml
```

Details, query examples and the honest limits of this topology:
[logs/README.md](logs/README.md).

### Managed clusters (EKS/AKS/GKE)

Set a real `storageClassName` in the values file, and if the etcd /
scheduler / controller-manager targets show permanently down, disable
those components as documented at the bottom of
`helm/values-kube-prometheus-stack.yaml` — managed control planes don't
expose them.

## Make it fire (5-minute demo)

```bash
# Generate steady traffic
kubectl -n demo run loadgen --image=curlimages/curl --restart=Never --command -- \
  sh -c 'while true; do curl -s -o /dev/null http://demo-app:8080/work; sleep 0.2; done'

# Break it: half of all requests now fail
kubectl -n demo set env deployment/demo-app ERROR_RATE=0.5

# Watch: Golden Signals error ratio climbs, SLO dashboard burn rate crosses
# 1x, then 14.4x — SLOFastBurn pages #alerts-critical with a runbook link.

# Fix it and clean up
kubectl -n demo set env deployment/demo-app ERROR_RATE=0.02
kubectl -n demo delete pod loadgen
```

## The SLO design

**SLI — availability, measured at the service.** Good events are requests
with status < 500, counted from the service's own `http_requests_total`.
Not uptime of a pod, not a synthetic probe: the fraction of real user
requests that didn't fail. Probe traffic (`/healthz`, `/metrics`) is
excluded by design in the demo app so it can't dilute the SLI.

**SLO — 99.9% over a rolling 30 days.** Error budget: 0.1% of requests —
roughly 43 minutes of full outage per month. Tight enough to matter, loose
enough to ship.

**Why burn rates, and why two windows per alert.** A threshold on the raw
error ratio ("page at >1% errors for 5m") pages on noise and misses slow
bleeds. Burn rate normalizes by budget: it answers *"how long until the
budget is gone at this rate?"* Each alert pairs a long window (the burn is
real, sustained) with a short window (it's still happening *now*), which
makes alerts fire fast on real incidents and reset quickly after the fix —
the multi-window pattern from the Google SRE Workbook:

| Alert | Burn | Windows | Budget consumed | Severity |
|---|---|---|---|---|
| SLOFastBurn | 14.4x | 1h + 5m | 2% in 1 hour | critical (page) |
| SLOSlowBurnPage | 6x | 6h + 30m | 5% in 6 hours | critical (page) |
| SLOBurnTicketFast | 3x | 1d + 2h | 10% in 1 day | warning (ticket) |
| SLOBurnTicketSlow | 1x | 3d + 6h | 10% in 3 days | warning (ticket) |

**Recording rules do the math once.** `job:http_error_ratio:rate<window>`
for 5m…30d plus `slo:error_budget_remaining:ratio30d` are precomputed every
30s, so dashboards stay fast and every alert/dashboard reads the *same*
numbers — no five slightly different copies of the SLI expression.

## Alert catalog

SLO alerts are above. Platform alerts watch the infrastructure that usually
*explains* a burn (all in `alerts/platform-alerts.yaml`):

| Alert | Severity | Meaning |
|---|---|---|
| PodCrashLooping | warning | Container restarted >3 times in 1h — [runbook](runbooks/pod-crashlooping.md) |
| DeploymentReplicasUnavailable | warning | Fewer available replicas than desired for 15m |
| NodeHighCPUUsage | warning | Node CPU >85% for 15m |
| NodeHighMemoryUsage | warning | Node memory >90% used for 15m |
| NodeMemoryPressure | critical | kubelet reports MemoryPressure — evictions may start |
| PersistentVolumeFillingUp | warning | PVC <15% space free |
| PersistentVolumeAlmostFull | critical | PVC <5% space free |
| KubeStateMetricsDown | critical | kube-state-metrics unscraped for 10m — all `kube_*` rules go blind |
| TargetDown | warning | A scrape target (other than core components) is down 15m |

The chart's curated default rules (API server, etcd, kubelet, node
filesystem, etc.) stay enabled alongside these. Every alert carries
`summary`, `description`, and a `runbook_url` — the two highest-value ones
resolve to full runbooks in [runbooks/](runbooks/).

## Dashboard tour

**Golden Signals — Service Overview** (`dashboards/golden-signals.json`).
Traffic, errors, latency, saturation for any service exposing
`http_requests_total` / `http_request_duration_seconds`: request rate by
status class, error ratio against the budget line, p50/p95/p99 latency,
per-pod CPU and memory. Variables: Prometheus datasource, `job`, `namespace`.

**SLO & Error Budget** (`dashboards/slo-error-budget.json`). Fed entirely
by the recording rules: 30d availability gauge (floor at 99% so movement is
visible), error-budget-remaining gauge with the 25% "freeze risky changes"
threshold, current 1h burn rate, burn rate by window against the 1x and
14.4x lines, and every window's error ratio side by side.

To adopt either dashboard for a real service: point a ServiceMonitor at it,
keep the `job` label stable, and — for the SLO view — copy the recording
rules with your job name and budget.

## Validation

Everything in this repo was checked before commit, in this environment:

- `helm lint` + `helm template` of kube-prometheus-stack 92.11.0 with
  these values (126 objects; Prometheus retention/storage/selectors and
  the Alertmanager Slack config inspected in the render), and of the
  Loki/Promtail charts with the log values.
- `promtool check rules` over all 22 rules in `alerts/` (converted from
  the PrometheusRule CRDs) and over all 36 PromQL expressions used by the
  dashboards.
- Dashboard JSON parses; the ConfigMaps round-trip (YAML → JSON) cleanly.
- `pytest` for the demo app: 5 tests (endpoints, metrics exposition,
  error path).
- Not validated: an end-to-end run against a live cluster (no cluster in
  this environment) — install steps follow the charts' documented flows.

## What this demonstrates

- **SLO engineering** — SLI selection, error budgets, multi-window burn
  rates, and the judgement to alert on user impact instead of symptoms.
- **Prometheus Operator fluency** — ServiceMonitor/PrometheusRule
  discovery labels, recording-rule naming conventions, scrape/rule
  namespace selectors.
- **Grafana as code** — sidecar provisioning, ConfigMap-generated
  dashboards, templating, thresholds that encode policy (budget freeze
  at 25%).
- **Alert hygiene** — severity routing, inhibition (a critical mutes its
  warning twin), secrets via mounted files, runbook links on the alerts
  that page.
- **Full-stack observability thinking** — metrics, logs (same labels,
  joined in Explore), and a workload instrumented to be observed,
  including keeping probe traffic out of the SLI.
- **Platform pragmatism** — pinned chart versions, sized resources,
  retention trade-offs, managed-cluster caveats, and a documented
  migration path (Promtail → Alloy, local TSDB → Thanos/Mimir) instead
  of pretending the demo topology is the final one.

## License

MIT — see [LICENSE](LICENSE). © 2026 Venkateswarlu Maddula.
