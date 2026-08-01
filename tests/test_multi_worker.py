"""Two uvicorn workers actually serve the same session (review finding H3).

Every prior test runs in one process. This one starts a real multi-worker
uvicorn — the thing the deployment pin forbade — and proves the claim end to
end rather than by inference:

  * a session is inserted straight into Postgres, so NEITHER worker created it;
  * many requests are then served, distributed across workers by the OS;
  * every one must succeed. Under the old in-process registry, a worker that
    had not created the session returned 404, so a mixed run would have failed
    part way.

No LLM is involved: the endpoints exercised read state rather than advance a
conversation, which is exactly the part that used to be process-local.

Requires TEST_DATABASE_URL and binds a local port.
"""

import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to run the multi-worker tests",
)

WORKERS = 2
REQUESTS = 60


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    """A real uvicorn with two worker processes."""
    port = _free_port()
    env = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL,
        "API_SECRET_KEY": "multi-worker-test-key",
        "ANTHROPIC_API_KEY": "sk-not-used-by-these-endpoints",
        "ENVIRONMENT": "development",
        "WEB_CONCURRENCY": str(WORKERS),
        "PYTHONPATH": os.getcwd(),
    }

    # Log to a file, not a pipe: two workers fill a PIPE's buffer and the
    # server blocks forever waiting for a reader that only runs after startup.
    import tempfile

    log = tempfile.NamedTemporaryFile(
        prefix="kk-multiworker-", suffix=".log", delete=False, mode="w+"
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.routes:app",
         "--host", "127.0.0.1", "--port", str(port), "--workers", str(WORKERS)],
        env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
    )

    def _log_contents() -> str:
        log.flush()
        with open(log.name) as f:
            return f.read()

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 90
    import urllib.error
    import urllib.request

    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"uvicorn exited early:\n{_log_contents()}")
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=2) as r:
                if r.status == 200:
                    break
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.3)
    else:
        proc.kill()
        pytest.fail(f"uvicorn did not become healthy in time:\n{_log_contents()}")

    # Uvicorn logs one "Started server process [pid]" per worker.
    started = _log_contents().count("Started server process")
    assert started >= WORKERS, (
        f"expected {WORKERS} workers, uvicorn started {started}:\n{_log_contents()}"
    )

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()
    try:
        os.unlink(log.name)
    except OSError:
        pass


def _seed_session(stage: int = 1) -> str:
    """Insert a session directly, so no worker has ever seen it."""
    import uuid

    import psycopg

    session_id = str(uuid.uuid4())
    now = time.time()
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO kk_sessions (session_id, stage, data, created_at, expires_at) "
            "VALUES (%s, %s, %s::jsonb, %s, %s)",
            (session_id, stage, json.dumps({}), now, now + 86400),
        )
    return session_id


def _token(session_id: str, scope: str = "manager") -> str:
    from config.settings import settings

    original = settings.api_secret_key
    object.__setattr__(settings, "api_secret_key", "multi-worker-test-key")
    try:
        from api.auth import issue_token

        return issue_token(session_id, scope)
    finally:
        object.__setattr__(settings, "api_secret_key", original)


def _get(base: str, path: str, token: str | None = None):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(f"{base}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class TestAnyWorkerCanServeAnySession:
    def test_the_server_really_has_multiple_workers(self, server):
        """Guards the rest of the file: one worker would prove nothing."""
        import psycopg

        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            # Each worker opens its own pools, so several backends are connected.
            backends = conn.execute(
                "SELECT count(DISTINCT pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND application_name <> 'psql'"
            ).fetchone()[0]
        assert backends >= WORKERS, (
            f"expected at least {WORKERS} worker connections, saw {backends}"
        )

    def test_a_session_no_worker_created_is_served_by_all_of_them(self, server):
        session_id = _seed_session(stage=1)
        token = _token(session_id)

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(
                lambda _: _get(server, f"/api/sessions/{session_id}/status", token),
                range(REQUESTS),
            ))

        codes = [status for status, _ in results]
        assert set(codes) == {200}, (
            f"some requests failed — a worker did not know the session: "
            f"{sorted(set(codes))}"
        )

    def test_an_unknown_session_is_consistently_404(self, server):
        """The opposite case: absence must be consistent too, not worker-dependent."""
        token = _token("no-such-session")

        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = [
                status
                for status, _ in pool.map(
                    lambda _: _get(server, "/api/sessions/no-such-session/status", token),
                    range(24),
                )
            ]

        assert set(codes) == {404}, f"inconsistent across workers: {sorted(set(codes))}"

    def test_tokens_are_accepted_by_every_worker(self, server):
        """The signing key must be shared, not generated per process.

        With API_SECRET_KEY unset each worker invents its own ephemeral key, so
        a token minted by one is rejected by the others — intermittent 401s.
        """
        session_id = _seed_session(stage=2)
        token = _token(session_id)

        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = [
                status
                for status, _ in pool.map(
                    lambda _: _get(server, f"/api/sessions/{session_id}/status", token),
                    range(32),
                )
            ]

        assert 401 not in codes, "a worker rejected a token another worker would accept"
        assert set(codes) == {200}


class TestAdvisoryLocksSpanProcesses:
    def test_two_processes_cannot_hold_the_same_session_lock(self):
        """In-process locks stop at the process boundary; advisory locks do not."""
        from api.postgres_store import PostgresSessionLocks

        a = PostgresSessionLocks(conninfo=TEST_DATABASE_URL)
        b = PostgresSessionLocks(conninfo=TEST_DATABASE_URL)
        try:
            order = []

            with a.lock("contended-session"):
                order.append("a-acquired")

                def second():
                    with b.lock("contended-session"):
                        order.append("b-acquired")

                import threading

                t = threading.Thread(target=second)
                t.start()
                time.sleep(0.4)
                # b must still be waiting on a's lock.
                order.append("a-releasing")

            t.join(timeout=10)
            assert order == ["a-acquired", "a-releasing", "b-acquired"], (
                f"the second holder did not wait: {order}"
            )
        finally:
            a.close()
            b.close()

    def test_different_sessions_do_not_block_each_other(self):
        from api.postgres_store import PostgresSessionLocks

        locks = PostgresSessionLocks(conninfo=TEST_DATABASE_URL)
        try:
            started = time.time()
            with locks.lock("session-one"):
                with locks.lock("session-two"):
                    pass
            assert time.time() - started < 5, "unrelated sessions serialised"
        finally:
            locks.close()

    def test_the_key_is_stable_and_well_distributed(self):
        from api.postgres_store import advisory_key

        assert advisory_key("abc") == advisory_key("abc")
        assert advisory_key("abc") != advisory_key("abd")
        # Must fit a signed 64-bit integer, which is what pg_advisory_lock takes.
        for sid in ("a", "b" * 100, "session-with-dashes-123"):
            assert -(2 ** 63) <= advisory_key(sid) < 2 ** 63
