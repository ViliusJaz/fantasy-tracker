"""Network resilience: what is retried, how long it waits, and when a source is paused."""
import gzip
import io
import json
import socket
import urllib.error
import urllib.request
from email.message import Message

import pytest

from backend import net


class FakeResponse(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://x", code, "err", headers, io.BytesIO(b""))


@pytest.fixture
def server(monkeypatch):
    """Scripted upstream: server.answers is a list of bodies (bytes) or exceptions, one per try."""
    class Server:
        answers, calls, waits = [], 0, []
    srv = Server()

    def urlopen(req, *args, **kwargs):
        srv.calls += 1
        answer = srv.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer if isinstance(answer, FakeResponse) else FakeResponse(answer)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(net.time, "sleep", srv.waits.append)
    monkeypatch.setattr(net, "NO_WAIT", False)
    net.reset()
    yield srv
    net.reset()


def test_success_first_time(server):
    server.answers = [b'{"a": 1}']
    assert net.fetch("https://x", "test", as_json=True) == {"a": 1}
    assert server.calls == 1 and server.waits == []
    assert net.stats()["test"] == {"requests": 1, "retries": 0, "failures": 0, "bytes": 8}


def test_gzip_answers_are_unpacked(server):
    server.answers = [FakeResponse(gzip.compress(b"hello"), {"Content-Encoding": "gzip"})]
    assert net.fetch("https://x", "test") == b"hello"


@pytest.mark.parametrize("error", [http_error(503), http_error(502), http_error(500), http_error(504),
                                   TimeoutError("slow"), ConnectionResetError(),
                                   urllib.error.URLError(socket.gaierror(8, "nodename nor servname"))])
def test_temporary_errors_are_retried_with_growing_waits(server, error):
    server.answers = [error, error, b"ok"]
    assert net.fetch("https://x", "test") == b"ok"
    assert server.calls == 3
    first, second = server.waits
    assert 0.7 <= first <= 1.3 and 1.4 <= second <= 2.6  # 1 s then 2 s, with jitter
    assert net.stats()["test"]["retries"] == 2 and net.stats()["test"]["failures"] == 0


@pytest.mark.parametrize("error", [http_error(403), http_error(404), http_error(400)])
def test_permanent_errors_are_not_retried(server, error):
    server.answers = [error]
    with pytest.raises(net.FetchError) as info:
        net.fetch("https://x", "test")
    assert server.calls == 1 and server.waits == []
    assert info.value.status == error.code


def test_gives_up_after_three_tries(server):
    server.answers = [http_error(503)] * 3
    with pytest.raises(net.FetchError, match="HTTP 503"):
        net.fetch("https://x", "test")
    assert server.calls == 3 and len(server.waits) == 2
    assert net.stats()["test"]["failures"] == 1


def test_retry_after_is_honoured_and_capped(server):
    server.answers = [http_error(429, retry_after=7), http_error(429, retry_after=999), b"ok"]
    assert net.fetch("https://x", "test") == b"ok"
    assert server.waits == [7.0, net.MAX_WAIT]


def test_html_instead_of_json_fails_at_once(server):
    server.answers = [b"<!DOCTYPE html><html><title>Just a moment...</title></html>"]
    with pytest.raises(net.FetchError, match="HTML"):
        net.fetch("https://x", "test", as_json=True)
    assert server.calls == 1


def test_cut_off_json_is_retried(server):
    server.answers = [b'{"data": {"a"', json.dumps({"data": 1}).encode()]
    assert net.fetch("https://x", "test", as_json=True) == {"data": 1}
    assert server.calls == 2


def test_broken_gzip_is_retried(server):
    server.answers = [FakeResponse(b"not gzip", {"Content-Encoding": "gzip"}), b"ok"]
    assert net.fetch("https://x", "test") == b"ok"


def test_source_is_paused_after_repeated_failures(server):
    server.answers = [http_error(404)] * net.OPEN_AFTER
    for _ in range(net.OPEN_AFTER):
        with pytest.raises(net.FetchError):
            net.fetch("https://x", "test")
    calls = server.calls
    with pytest.raises(net.FetchError, match="paused"):
        net.fetch("https://x", "test")
    assert server.calls == calls  # no request at all while paused
    server.answers = [b"ok"]
    assert net.fetch("https://x", "other") == b"ok"  # other sources are not affected


def test_a_success_resets_the_failure_count(server):
    server.answers = [http_error(404)] * (net.OPEN_AFTER - 1) + [b"ok"] + [http_error(404)]
    for _ in range(net.OPEN_AFTER - 1):
        with pytest.raises(net.FetchError):
            net.fetch("https://x", "test")
    assert net.fetch("https://x", "test") == b"ok"
    with pytest.raises(net.FetchError, match="HTTP 404"):
        net.fetch("https://x", "test")  # counting started over: not paused


def test_graphql_errors_become_upstream_errors(ft, monkeypatch):
    monkeypatch.setattr(ft, "fetch", lambda *a, **k: {"errors": [{"message": "bad field"}]})
    with pytest.raises(ft.UpstreamError, match="bad field"):
        ft.gql("query { x }", {})
    def down(*a, **k):
        raise net.FetchError("HTTP 503")
    monkeypatch.setattr(ft, "fetch", down)
    with pytest.raises(ft.UpstreamError, match="HTTP 503"):
        ft.gql("query { y }", {})


def test_parallel_identical_queries_share_one_request(ft, monkeypatch):
    import threading
    import time
    calls = []

    def slow(*a, **k):
        calls.append(1)
        time.sleep(0.2)
        return {"data": {"n": 1}}
    monkeypatch.setattr(ft, "fetch", slow)
    results = []
    threads = [threading.Thread(target=lambda: results.append(ft.gql("query { same }", {"v": 1}))) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [{"n": 1}] * 6 and len(calls) == 1
