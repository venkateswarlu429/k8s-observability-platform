"""Tests for demo-app. Run:  pip install -r requirements-dev.txt && pytest"""

import json
import threading
import urllib.error
import urllib.request

import pytest

import app as demo


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


@pytest.fixture(scope="module")
def server():
    demo.CONFIG["error_rate"] = 0.0
    srv = demo.make_server(0)  # ephemeral port
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_healthz(server):
    status, body = get(server + "/healthz")
    assert status == 200
    assert json.loads(body)["status"] == "ok"


def test_root(server):
    status, body = get(server + "/")
    assert status == 200
    assert json.loads(body)["service"] == "demo-app"


def test_work_success_and_metrics(server):
    status, _ = get(server + "/work")
    assert status == 200
    status, metrics = get(server + "/metrics")
    assert status == 200
    assert 'http_requests_total{method="GET",path="/work",status="200"}' in metrics
    assert "http_request_duration_seconds_bucket" in metrics


def test_work_error_path(server):
    demo.CONFIG["error_rate"] = 1.0
    try:
        status, body = get(server + "/work")
        assert status == 500
        assert "error" in json.loads(body)
    finally:
        demo.CONFIG["error_rate"] = 0.0
    _, metrics = get(server + "/metrics")
    assert 'status="500"' in metrics


def test_not_found(server):
    status, _ = get(server + "/nope")
    assert status == 404
