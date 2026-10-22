# Runbook: SLO burn-rate page (SLOFastBurn / SLOSlowBurnPage)

**Alerts:** `SLOFastBurn` (14.4x, 1h+5m) · `SLOSlowBurnPage` (6x, 6h+30m)
**Severity:** critical — a human is paged; act now.
**Meaning:** users are seeing 5xx errors fast enough to consume a dangerous
share of the 30-day error budget (2% in 1h, or 5% in 6h). This is a
user-impact alert, not an infrastructure hunch — trust it.

## 1. Confirm (2 minutes)

1. Open the **SLO & Error Budget** dashboard. Check *Current burn rate (1h)*
   and *Error ratio by window* — both the long and short windows should be
   elevated (that's why the alert fired).
2. Open **Golden Signals**: which status class grew — 5xx only, or 4xx too?
   A 4xx spike points at a bad client deploy or changed API contract, which
   does **not** burn this SLO (5xx only) — if 5xx is flat, suspect the
   recording rules or a label change, not the service.

## 2. Find the cause (5–10 minutes)

1. **What changed?** `kubectl -n <ns> rollout history deployment/<svc>` and
   check the deploy/config/flag changes in the last 2 hours. Most fast burns
   are a bad deploy.
2. **Which path?** In Prometheus:
   `sum by (path) (rate(http_requests_total{job="demo-app",status=~"5.."}[15m]))`
3. **What do the errors say?** Grafana → Explore → Loki:
   `{namespace="demo", app="demo-app"} |= "error"` for the alert window.
4. **Is a dependency down?** Check the Golden Signals saturation panels and
   the platform alerts firing alongside (NodeMemoryPressure,
   PersistentVolumeAlmostFull, PodCrashLooping) — a platform alert in the
   same window is usually the root cause, not a coincidence.

## 3. Mitigate

- Bad deploy → **roll back first, debug second**:
  `kubectl -n <ns> rollout undo deployment/<svc>`
- One bad pod/replica → delete it and watch the error ratio fall.
- Dependency saturated → scale the dependency or shed load; do not scale
  the erroring service into a failing dependency.
- If errors are caused by a single abusive client, rate-limit at ingress
  and note it in the incident — the budget math does not care whose fault
  it is, but the postmortem should.

## 4. Know when you're done

The alert auto-resolves when the short window drops below the threshold —
that is the point of the multi-window design. Do not close the incident
until the **1h burn rate is under 1x** and falling.

## 5. After the page

- Check *Error budget remaining* on the SLO dashboard. **Below 25%:** freeze
  non-essential releases for the service until the window recovers.
  **At 0%:** the SLO is breached — the work becomes reliability work
  (error-budget policy), not feature work, until it recovers.
- File the postmortem within 2 business days: timeline, root cause, budget
  consumed (the dashboard shows it), and one preventative action.
- Ticket-severity burns (`SLOBurnTicketFast/Slow`) follow the same steps
  minus the urgency: investigate the same business day.
