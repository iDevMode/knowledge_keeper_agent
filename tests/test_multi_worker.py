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
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to run the multi-worker tests",
)

WORKERS = 2
REQUESTS = 60

# Uvicorn logs exactly one of these per worker process it spawns.
_STARTED = re.compile(r"Started server process \[(\d+)\]")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _process_alive(pid: int) -> bool:
    """True if pid is still running.

    Deliberately not os.kill(pid, 0): on Windows os.kill ignores the signal and
    calls TerminateProcess, so the liveness probe would kill what it measures.
    """
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True,
        ).stdout
        return any(
            len(parts) > 1 and parts[1] == str(pid)
            for parts in (line.split() for line in out.splitlines())
        )
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@dataclass
class _Server:
    base: str
    proc: subprocess.Popen
    log_path: str
    worker_pids: list = field(default_factory=list)

    def log_contents(self) -> str:
        with open(self.log_path) as f:
            return f.read()


def _start_server() -> _Server:
    """Start a real uvicorn with WORKERS worker processes and wait for all of them."""
    port = _free_port()
    env = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL,
        "API_SECRET_KEY": "multi-worker-test-key",
        "ANTHROPIC_API_KEY": "sk-not-used-by-these-endpoints",
        "ENVIRONMENT": "development",
        "WEB_CONCURRENCY": str(WORKERS),
        "PYTHONPATH": os.getcwd(),
        # This file runs up to two servers at once (the module fixture's, plus
        # the one the teardown tests start), and the production default budget
        # of 2*5 + 10 per worker would demand ~80 connections against the 100 a
        # default Postgres allows. Requests then block on a free pool slot and
        # time out, which reads as a product failure and is not one. These
        # tests are about worker semantics, not pool sizing.
        "DB_POOL_SIZE": "2",
        "DB_LOCK_POOL_SIZE": "2",
    }

    # Log to a file, not a pipe: two workers fill a PIPE's buffer and the
    # server blocks forever waiting for a reader that only runs after startup.
    import tempfile

    log = tempfile.NamedTemporaryFile(
        prefix="kk-multiworker-", suffix=".log", delete=False, mode="w+"
    )
    log.close()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.routes:app",
         "--host", "127.0.0.1", "--port", str(port), "--workers", str(WORKERS)],
        env=env, stdout=open(log.name, "w"), stderr=subprocess.STDOUT, text=True,
    )
    server = _Server(base=f"http://127.0.0.1:{port}", proc=proc, log_path=log.name)

    # Wait for every worker, not just for the port to answer. A health check
    # passes as soon as ONE worker is serving, so gating on it and then counting
    # workers is a race — that race is what made this file flaky.
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"uvicorn exited early:\n{server.log_contents()}")
        pids = _STARTED.findall(server.log_contents())
        if len(set(pids)) >= WORKERS:
            server.worker_pids = sorted({int(p) for p in pids})
            break
        time.sleep(0.2)
    else:
        _stop_server(server)
        pytest.fail(
            f"expected {WORKERS} workers within 90s:\n{server.log_contents()}"
        )

    # And that it actually serves.
    import urllib.error
    import urllib.request

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{server.base}/api/health", timeout=2) as r:
                if r.status == 200:
                    break
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)
    else:
        _stop_server(server)
        pytest.fail(f"uvicorn never became healthy:\n{server.log_contents()}")

    return server


def _stop_server(server: _Server) -> None:
    """Stop the supervisor AND its workers.

    proc.terminate() is not enough on Windows: it is TerminateProcess against
    the supervisor, which therefore never shuts its multiprocessing workers
    down. They are orphaned holding a Postgres pool each (~14 connections per
    run measured), so repeated runs exhaust max_connections and the suite starts
    failing with 500s that look like product bugs. POSIX gets SIGTERM, which
    uvicorn handles by stopping its workers.
    """
    proc = server.proc
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    try:
        os.unlink(server.log_path)
    except OSError:
        pass


def _app_connections() -> int:
    import psycopg

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        return conn.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        ).fetchone()[0]


@pytest.fixture(scope="module")
def running_server():
    """A real uvicorn with two worker processes.

    Teardown is asserted, not assumed. proc.terminate() on Windows orphaned
    both workers, each holding a Postgres pool: connections climbed 6 -> 20 ->
    34 -> 48 over three runs, and after about seven the database refused new
    clients with "too many clients already", which surfaced as 500s in
    unrelated tests here and looked like a product bug. Nothing checked, so it
    stayed invisible until the database ran out.

    Measured at the database rather than by polling worker pids: the tests
    above have already driven real queries through both workers, so the pools
    are genuinely open, and a connection count cannot be fooled by Windows
    recycling a pid.
    """
    baseline = _app_connections()
    s = _start_server()
    try:
        yield s
    finally:
        _stop_server(s)

    deadline = time.time() + 20
    while time.time() < deadline:
        after = _app_connections()
        if after <= baseline:
            break
        time.sleep(0.3)
    else:
        pytest.fail(
            f"teardown leaked database connections: {baseline} before the server "
            f"started, {after} after it stopped — orphaned workers still hold "
            f"their pools, and repeated runs will exhaust max_connections"
        )


@pytest.fixture(scope="module")
def server(running_server) -> str:
    """Base URL of the running server, for the tests that only need to call it."""
    return running_server.base


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
    def test_the_server_really_has_multiple_workers(self, running_server):
        """Guards the rest of the file: one worker would prove nothing.

        Counts worker PROCESSES, from the pids uvicorn logs as it spawns them.
        It used to count rows in pg_stat_activity, which is not a worker count:
        a single worker with db_pool_size=5 satisfies ">= 2 backends" on its
        own, and any connection left over from an earlier run satisfies it too.
        So the guard passed most reliably when the database was dirty enough to
        make it meaningless, and failed against a clean one because it ran
        before the pools were opened.
        """
        # Re-read the log rather than using the pids captured at startup: the
        # supervisor respawns a worker that dies, so the log accumulates more
        # "Started server process" lines than there are live workers, and an
        # older pid in that list is legitimately gone.
        logged = {int(p) for p in _STARTED.findall(running_server.log_contents())}
        assert running_server.proc.pid not in logged, (
            "counted the supervisor as a worker"
        )
        alive = sorted(pid for pid in logged if _process_alive(pid))
        assert len(alive) >= WORKERS, (
            f"expected {WORKERS} live worker processes, found {alive} among "
            f"logged {sorted(logged)}:\n{running_server.log_contents()}"
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
