import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import JobInput, OpenAIProvider, ProviderError, SampleProvider, Store, create_app, process_one, worker_lock
from run_demo import bind_local_port


def enqueue(db):
    return db.create(JobInput(message="Please check delivery for order 1042."), "hardening-key")[0]


@pytest.mark.parametrize("value", ["", " ", "unknown"])
def test_invalid_mode_is_rejected_before_creating_data(tmp_path, monkeypatch, value):
    monkeypatch.setenv("PROOFKIT_MODE", value)
    path = tmp_path / "invalid.sqlite3"
    with pytest.raises(ValueError, match="PROOFKIT_MODE must be sample or live"):
        create_app(path)
    assert not path.exists()
    with pytest.raises(ValueError, match="PROOFKIT_MODE must be sample or live"):
        create_app(path, mode=value)


@pytest.mark.parametrize("value", ["", " ", ":memory:"])
def test_invalid_database_configuration_is_rejected(monkeypatch, value):
    monkeypatch.setenv("PROOFKIT_DB", value)
    with pytest.raises(ValueError, match="persistent SQLite file"):
        create_app(mode="sample")


@pytest.mark.parametrize("variable", ["OPENAI_API_KEY", "OPENAI_MODEL"])
def test_blank_live_configuration_fails_safely(tmp_path, monkeypatch, variable):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-credential")
    monkeypatch.setenv("OPENAI_MODEL", "synthetic-test-model")
    monkeypatch.setenv(variable, "   ")
    with pytest.raises(ValueError) as exc:
        create_app(tmp_path / "live.sqlite3", mode="live")
    assert str(exc.value) == "Live mode requires OPENAI_API_KEY and OPENAI_MODEL."


def test_missing_mode_defaults_to_sample_without_credentials(tmp_path, monkeypatch):
    for name in ("PROOFKIT_MODE", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path / "sample.sqlite3")) as client:
        assert client.get("/api/config").json()["mode"] == "sample"


def test_launcher_rejects_blank_database_before_creating_control_state(monkeypatch, capsys):
    from run_demo import main
    monkeypatch.setenv("PROOFKIT_DB", "")
    monkeypatch.setattr(sys, "argv", ["run_demo.py"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "PROOFKIT_DB must name a persistent SQLite file" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_repeated_crashes_consume_budget_without_fourth_attempt(tmp_path):
    db = Store(tmp_path / "restarts.sqlite3")
    job = enqueue(db)
    for attempt in range(1, 4):
        assert db.claim()["attempt"] == attempt
        db = Store(db.path)
        db.recover()
    assert not await process_one(db, SampleProvider())
    result = db.get(job["id"])
    assert result["attempt"] == 3 and result["status"] == "failed"
    assert result["result"] is None and db.metrics()["total"] == 1
    assert sum(e["kind"] == "running" for e in result["events"]) == 3


@pytest.mark.asyncio
async def test_claim_never_exceeds_budget_for_previously_queued_record(tmp_path):
    db = Store(tmp_path / "old.sqlite3")
    job = enqueue(db)
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET attempt=3 WHERE id=?", (job["id"],))
    assert not await process_one(db, SampleProvider())
    assert db.get(job["id"])["attempt"] == 3
    assert db.get(job["id"])["status"] == "failed"


def test_shutdown_finishes_inflight_and_keeps_pending_jobs(tmp_path):
    import threading
    entered = threading.Event()

    class SlowProvider(SampleProvider):
        async def triage(self, job):
            entered.set()
            await asyncio.sleep(0.15)
            return await super().triage(job)

    app = create_app(tmp_path / "shutdown.sqlite3", provider=SlowProvider())
    with TestClient(app) as client:
        job_id = client.post("/api/jobs", headers={"Idempotency-Key": "shutdown-one"},
                             json={"message": "Please check my delivery 1042."}).json()["id"]
        assert entered.wait(3)
        pending = client.post("/api/jobs", headers={"Idempotency-Key": "shutdown-two"},
                              json={"message": "Please check my invoice 2087."}).json()["id"]
    assert app.state.store.get(job_id)["status"] == "needs_review"
    assert app.state.store.get(pending)["status"] == "queued"
    with TestClient(create_app(app.state.store.path)) as client:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if client.get("/api/jobs/" + pending).json()["status"] == "needs_review":
                break
            time.sleep(0.02)
        assert client.get("/api/jobs/" + pending).json()["status"] == "needs_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [[], None, {"status": "completed", "output": [None]},
    {"status": "completed", "output": [{"content": [None]}]}])
async def test_malformed_provider_envelopes_return_safe_error(response):
    provider = OpenAIProvider("test-secret", "test-model", httpx.MockTransport(
        lambda _: httpx.Response(200, content=json.dumps(response))))
    with pytest.raises(ProviderError, match="^provider_invalid_response$"):
        await provider.triage({"payload": {"message": "Synthetic request"}})


@pytest.mark.asyncio
async def test_provider_timeout_retries_and_invalid_boolean_is_rejected(tmp_path):
    db = Store(tmp_path / "timeout.sqlite3")
    job = enqueue(db)

    def timeout(request):
        assert request.extensions["timeout"]["read"] == 25
        raise httpx.ReadTimeout("test-secret", request=request)

    provider = OpenAIProvider("test-secret", "test-model", httpx.MockTransport(timeout))
    await process_one(db, provider)
    data = db.get(job["id"])
    assert data["status"] == "retrying" and data["error"] == "provider_connection_failed"
    assert "test-secret" not in json.dumps(data)

    class InvalidBoolean(SampleProvider):
        async def triage(self, job):
            result, usage = await super().triage(job)
            result["needs_human"] = "true"
            return result, usage

    await process_one(db, InvalidBoolean(), now=time.time() + 60)
    assert db.get(job["id"])["error"] == "schema_validation_failed"


@pytest.mark.asyncio
async def test_usage_only_persists_numeric_token_counts():
    result, _ = await SampleProvider().triage({"payload": {"message": "Synthetic delivery request 1042", "scenario": "normal"}})
    provider = OpenAIProvider("test-secret", "test-model", httpx.MockTransport(lambda _: httpx.Response(200, json={
        "status": "completed", "output": [{"content": [{"type": "output_text", "text": json.dumps(result)}]}],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30, "raw": "test-secret"}})))
    _, usage = await provider.triage({"payload": {"message": "Synthetic request"}})
    assert usage["tokens"] == {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
    assert "test-secret" not in json.dumps(usage)


@pytest.mark.asyncio
async def test_incomplete_message_never_enters_review(tmp_path):
    db = Store(tmp_path / "incomplete.sqlite3")
    job = enqueue(db)
    raw, _ = await SampleProvider().triage({**job, "attempt": 1})
    provider = OpenAIProvider("test-secret", "test-model", httpx.MockTransport(lambda _: httpx.Response(200, json={
        "status": "completed", "output": [{"type": "message", "status": "in_progress",
        "content": [{"type": "output_text", "text": json.dumps(raw)}]}]})))
    await process_one(db, provider)
    data = db.get(job["id"])
    assert data["error"] == "provider_incomplete" and data["status"] == "failed"
    assert data["result"] is None


def test_launcher_skips_occupied_port():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        if port == 65535:
            pytest.skip("No next port")
        with bind_local_port(port) as selected:
            assert selected.getsockname()[0] == "127.0.0.1"
            assert port < selected.getsockname()[1] < port + 20


def test_second_worker_cannot_recover_active_jobs(tmp_path):
    db = Store(tmp_path / "exclusive.sqlite3")
    job = enqueue(db)
    with worker_lock(db.path):
        db.claim()
        with pytest.raises(RuntimeError, match="active worker"):
            with TestClient(create_app(db.path)):
                pass
        assert db.get(job["id"])["status"] == "running"
        assert db.get(job["id"])["attempt"] == 1
    with TestClient(create_app(db.path, provider=SampleProvider())):
        pass
    assert db.get(job["id"])["attempt"] <= 2


def test_launcher_reports_fallback_url_and_stop_command_exits_cleanly(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PROOFKIT_MODE": "sample", "PROOFKIT_DB": str(tmp_path / "launcher.sqlite3"),
           "PYTHONIOENCODING": "utf-8"}
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        if port == 65535:
            pytest.skip("No next port")
        proc = subprocess.Popen([sys.executable, "run_demo.py", "--port", str(port)],
                                cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8")
        try:
            url = proc.stdout.readline().split()[1]
            actual = int(url.rsplit(":", 1)[1])
            assert port < actual < port + 20
            with httpx.Client(base_url=url, timeout=1, trust_env=False) as client:
                deadline = time.monotonic() + 8
                while True:
                    try:
                        response = client.get("/health")
                        assert response.json()["status"] == "ok"
                        break
                    except httpx.TransportError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.05)
            stopped = subprocess.run([sys.executable, "run_demo.py", "--stop", "--port", str(actual)],
                                     cwd=root, env=env, capture_output=True, timeout=5)
            assert stopped.returncode == 0
            assert proc.wait(timeout=8) == 0
            assert "Application shutdown complete" in proc.stdout.read()
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
            proc.stdout.close()
