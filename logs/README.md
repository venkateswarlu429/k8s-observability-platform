# Logs — how they join the stack

Metrics tell you *that* something is wrong (and the burn-rate alerts page
you); logs tell you *what*. This directory adds the smallest honest log
pipeline to the platform:

```
pod stdout/stderr
      │  (kubelet log files on every node)
      ▼
Promtail DaemonSet ──push──► Loki (SingleBinary, 7d retention, 10Gi PVC)
                                   │
                                   ▼
                        Grafana "Loki" datasource
                     (sidecar-provisioned ConfigMap)
```

## Pieces

| File | What it is |
|---|---|
| `values-loki.yaml` | Loki chart (7.3.0) in **SingleBinary** mode: one process, filesystem storage, TSDB schema, 7-day retention enforced by the compactor. |
| `values-promtail.yaml` | Promtail chart (6.17.1) DaemonSet. Ships every pod's logs, unpacks CRI format, pushes to `loki:3100`. |
| `grafana-datasource-loki.yaml` | ConfigMap labelled `grafana_datasource="1"` — the Grafana sidecar from the main values file provisions Loki next to the default Prometheus datasource automatically. |

## Install

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm upgrade --install loki     grafana/loki     --version 7.3.0  -n monitoring -f logs/values-loki.yaml
helm upgrade --install promtail grafana/promtail --version 6.17.1 -n monitoring -f logs/values-promtail.yaml
kubectl apply -f logs/grafana-datasource-loki.yaml
```

## Querying

In Grafana → Explore → Loki:

```logql
{namespace="demo", app="demo-app"}                # everything from the demo app
{namespace="demo"} |= "error"                     # lines containing "error"
{namespace="demo", container="demo-app"} | json   # parse JSON log lines
```

The correlation story: an SLO alert fires → open the SLO dashboard → note
the time window → Explore in Loki with the same `namespace`/`app` labels
Prometheus uses. Labels are the join key between the two systems, which is
why Promtail's config keeps the label set deliberately small and identical
in spirit to the metric labels (`namespace`, `pod`, `container`, `app`).

## Deliberate limits

- **SingleBinary + filesystem** is a demo/small-cluster topology. The
  production path is `deploymentMode: SimpleScalable` with S3/GCS storage
  in the same chart — no changes needed here except the values.
- **7-day retention.** Logs are the most expensive signal per byte; metrics
  (15 days) outlive them on purpose.
- **Promtail is maintenance-mode upstream.** Grafana Alloy is the
  successor; the migration swaps only the shipper, not Loki or Grafana.
