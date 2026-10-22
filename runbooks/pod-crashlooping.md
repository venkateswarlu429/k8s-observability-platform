# Runbook: PodCrashLooping

**Alert:** `PodCrashLooping` — a container restarted more than 3 times in
an hour.
**Severity:** warning — investigate the same day; it becomes a page only
when it starts burning SLO budget.

## 1. Identify the failure mode

```bash
kubectl -n <namespace> describe pod <pod>
kubectl -n <namespace> get pod <pod> -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}'
```

The `reason` and the exit code tell you where to look:

| Reason / signal | Likely cause | Go to |
|---|---|---|
| `OOMKilled` | Memory limit too low, or a leak | §2 |
| `Error`, non-zero exit, logs show a stack trace | Application crash on startup | §3 |
| `Error`, logs complain about missing file/env/secret | Config or mount problem | §4 |
| `CreateContainerConfigError` / `ImagePullBackOff` in events | Bad secret/configmap/image reference | §4 |
| Liveness probe failing in events, app logs healthy | Probe misconfigured (path/port/timing) | §5 |
| `Completed` with restarts on a long-running pod | Process exits by design — should be a Job/CronJob | §3 |

## 2. OOMKilled

```bash
kubectl -n <namespace> top pod <pod> --containers
```

Compare working-set memory (Grafana → node/pod dashboards, or
`container_memory_working_set_bytes`) with the container limit. Raise the
limit **and** the request together, then look for the leak: a pod whose
memory climbs monotonically between restarts is leaking, and a bigger
limit only buys time.

## 3. Application crash

```bash
kubectl -n <namespace> logs <pod> -c <container> --previous   # the crash that just happened
kubectl -n <namespace> logs <pod> -c <container>              # current attempt
```

`--previous` is the step everyone forgets: the current logs are the *new*
container booting, the previous ones contain the actual failure. If the
crash started with a deploy, roll back first (`kubectl rollout undo`) and
debug the broken version offline.

## 4. Config / secret / image problems

- `kubectl -n <namespace> get events --sort-by=.lastTimestamp` — the events
  name the missing Secret/ConfigMap/key directly.
- Verify the referenced objects exist **in the pod's namespace** and the
  keys match what the Deployment expects.
- ImagePull: check the image tag exists in the registry and the node can
  pull it (imagePullSecrets, private registry auth, rate limits).

## 5. Probe problems

If the app logs look healthy but kubelet keeps killing the container, the
liveness probe is the suspect: wrong path/port, or `initialDelaySeconds`
shorter than the app's real startup time under load. Prefer fixing the
probe timing over widening it blindly — and check whether the *readiness*
probe would be the right tool instead (liveness restarts; readiness just
removes the pod from the Service).

## Done when

Restarts stop for a full hour (the alert window), the pod is `Ready`, and —
if this was user-facing — the service's error ratio on the Golden Signals
dashboard is back at baseline. Note the root cause in the ticket; repeated
crashloops on the same workload are a reliability bug, not an ops chore.
