"""demo-app: a tiny workload that exists to be observed.

It exposes the two metric families the whole platform is built around:

  http_requests_total{method,path,status}        (counter)
  http_request_duration_seconds{method,path}     (histogram)

Endpoints:
  GET /         service info (counted as a normal request)
  GET /work     simulated business request: sleeps a random latency and
                fails with HTTP 500 roughly ERROR_RATE of the time, so the
                SLO burn-rate alerts can be exercised on demand
  GET /healthz  liveness/readiness probe target (NOT counted — probe
                traffic must not pollute the availability SLI)
  GET /metrics  Prometheus exposition (not counted)

Configuration via environment:
  PORT        listen port            (default 8080)
  ERROR_RATE  fraction of /work requests that fail (default 0.02)
"""

import json
import os
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Histogram,
    generate_latest,
)

SERVICE_NAME = "demo-app"

CONFIG = {
    "error_rate": float(os.environ.get("ERROR_RATE", "0.02")),
}

REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests by method, path and response status.",
    ["method", "path", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds by method and path.",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)


def simulate_work() -> tuple[int, dict]:
    """One unit of fake business work. Returns (status, payload)."""
    # Latency: mostly fast, with a long tail — like a real upstream call.
    time.sleep(random.lognormvariate(-3.0, 0.6))
    if random.random() < CONFIG["error_rate"]:
        return 500, {"error": "simulated upstream failure"}
    return 200, {"result": "ok"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep stdout quiet; logs go to Loki via stdout in real apps
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path == "/metrics":
            self._send(200, generate_latest(REGISTRY), CONTENT_TYPE_LATEST)
            return
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return

        started = time.perf_counter()
        if self.path == "/":
            status, payload = 200, {"service": SERVICE_NAME, "status": "ok"}
        elif self.path == "/work":
            status, payload = simulate_work()
        else:
            status, payload = 404, {"error": "not found"}
        elapsed = time.perf_counter() - started

        REQUESTS.labels(method="GET", path=self.path, status=str(status)).inc()
        LATENCY.labels(method="GET", path=self.path).observe(elapsed)
        self._send_json(status, payload)


def make_server(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = make_server(port)
    print(f"{SERVICE_NAME} listening on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
