import asyncio
import json
import time

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import JobInput, OpenAIProvider, ProviderError, SampleProvider, Store, Triage, create_app, process_one


def request(scenario="normal"):
    return JobInput(message="My order 1042 is delayed. Please check the parcel tracking.", scenario=scenario)


def store(tmp_path):
    return Store(tmp_path / "jobs.sqlite3")


def test_idempotency_conflict_and_persistence(tmp_path):
    db = store(tmp_path)
    first, created = db.create(request(), "request-one")
    assert created
    duplicate, created = db.create(request(), "request-one")
    assert not created and first["id"] == duplicate["id"]
    with pytest.raises(HTTPException) as exc:
        db.create(JobInput(message="A completely different support message."), "request-one")
    assert exc.value.status_code == 409
    reopened = store(tmp_path)
    assert len(reopened.list()) == 1
    assert reopened.get(first["id"])["status"] == "queued"


def test_queue_claim_is_exclusive(tmp_path):
    db = store(tmp_path)
    db.create(request(), "request-one")
    assert db.claim()["attempt"] == 1
    assert db.claim() is None


@pytest.mark.asyncio
async def test_validation_review_edit_and_approval_boundary(tmp_path):
    db = store(tmp_path)
    job, _ = db.create(request(), "request-one")
    with pytest.raises(HTTPException) as exc:
        db.approve(job["id"], Triage(category="shipping", priority="normal", summary="A delayed order.", suggested_reply="We will check the tracking details.", needs_human=True, missing_information=[]))
    assert exc.value.status_code == 409
    await process_one(db, SampleProvider())
    ready = db.get(job["id"])
    assert ready["status"] == "needs_review" and ready["result"]["category"] == "shipping"
    result = Triage.model_validate({**ready["result"], "summary": "Reviewer corrected this summary."})
    approved = db.approve(job["id"], result)
    assert approved["status"] == "approved"
    assert approved["approved_result"]["summary"] == "Reviewer corrected this summary."
    assert approved["result"]["summary"] != approved["approved_result"]["summary"]
    with pytest.raises(HTTPException):
        db.approve(job["id"], result)


@pytest.mark.asyncio
async def test_transient_failure_recovers_and_keeps_one_job(tmp_path):
    db = store(tmp_path)
    job, _ = db.create(request("transient"), "request-one")
    await process_one(db, SampleProvider())
    assert db.get(job["id"])["status"] == "retrying"
    assert not await process_one(db, SampleProvider())  # Backoff is respected.
    await process_one(db, SampleProvider(), now=time.time() + 60)
    recovered = db.get(job["id"])
    assert recovered["status"] == "needs_review" and recovered["attempt"] == 2
    assert len(db.list()) == 1
    assert [e["kind"] for e in recovered["events"]] == ["queued", "running", "retrying", "running", "validated"]


@pytest.mark.asyncio
async def test_outage_stops_at_retry_limit(tmp_path):
    db = store(tmp_path)
    job, _ = db.create(request("permanent"), "request-one")
    for _ in range(3):
        await process_one(db, SampleProvider(), now=time.time() + 60)
    failed = db.get(job["id"])
    assert failed["status"] == "failed" and failed["attempt"] == 3
    assert not await process_one(db, SampleProvider(), now=time.time() + 60)
    assert failed["result"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,code", [("invalid_output", "schema_validation_failed"), ("refusal", "sample_refusal")])
async def test_invalid_output_and_refusal_cannot_reach_review(tmp_path, scenario, code):
    db = store(tmp_path)
    job, _ = db.create(request(scenario), "request-one")
    await process_one(db, SampleProvider())
    data = db.get(job["id"])
    assert data["status"] == "failed" and data["error"] == code and data["attempt"] == 1


@pytest.mark.asyncio
async def test_interrupted_job_recovered_without_duplicate_record(tmp_path):
    db = store(tmp_path)
    job, _ = db.create(request(), "request-one")
    db.claim()  # Simulate a process disappearing during an attempt.
    reopened = store(tmp_path)
    reopened.recover()
    await process_one(reopened, SampleProvider())
    data = reopened.get(job["id"])
    assert data["status"] == "needs_review" and data["attempt"] == 2
    assert any(e["kind"] == "recovered" for e in data["events"])
    assert len(reopened.list()) == 1


def test_api_validation_same_origin_and_not_found(tmp_path):
    app = create_app(tmp_path / "api.sqlite3", start_worker=False)
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/").status_code == 200
        assert client.get("/").headers["content-security-policy"].startswith("default-src")
        assert client.get("/api/config").json()["mode"] == "sample"
        assert client.post("/api/jobs", json={"message": "too short"}).status_code == 422
        headers = {"Idempotency-Key": "api-request-one"}
        assert client.post("/api/jobs", json={"message": "A valid support request.", "unknown": "x"}, headers=headers).status_code == 422
        assert client.post("/api/jobs", json=request().model_dump(), headers={**headers, "Origin": "https://attacker.example"}).status_code == 403
        assert client.get("/health", headers={"Host": "attacker.example"}).status_code == 421
        a = client.post("/api/jobs", json=request().model_dump(), headers=headers)
        b = client.post("/api/jobs", json=request().model_dump(), headers=headers)
        assert a.status_code == 202 and b.json()["deduplicated"]
        assert a.json()["id"] == b.json()["id"]
        assert client.get("/api/metrics").json()["total"] == 1
        assert client.get("/api/jobs/missing").status_code == 404


def test_real_background_worker_processes_api_job(tmp_path):
    with TestClient(create_app(tmp_path / "background.sqlite3")) as client:
        response = client.post("/api/jobs", json=request().model_dump(), headers={"Idempotency-Key": "background-one"})
        job_id = response.json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = client.get("/api/jobs/" + job_id).json()
            if job["status"] == "needs_review":
                break
            time.sleep(0.02)
        assert job["status"] == "needs_review"
        assert client.post("/api/jobs/" + job_id + "/approve", json={"result": job["result"]}).json()["status"] == "approved"


def test_live_configuration_is_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with pytest.raises(ValueError):
        create_app(tmp_path / "live.sqlite3", mode="live")
    app = create_app(tmp_path / "live.sqlite3", provider=SampleProvider(), start_worker=False, mode="live")
    with TestClient(app) as client:
        assert client.post("/api/jobs", json=request("transient").model_dump(), headers={"Idempotency-Key": "live-request"}).status_code == 422


@pytest.mark.asyncio
async def test_live_adapter_request_schema_and_usage_with_mock_transport():
    result = {"category": "shipping", "priority": "normal", "summary": "Customer asks about delivery.", "suggested_reply": "We will check the order details before confirming.", "needs_human": True, "missing_information": []}
    def handler(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test-only-not-a-real-key"
        assert request.extensions["timeout"]["read"] == 25
        assert body["max_output_tokens"] == 1200
        assert body["text"]["format"]["type"] == "json_schema"
        assert set(body["text"]["format"]["schema"]["required"]) == set(Triage.model_fields)
        assert body["store"] is False and body["text"]["format"]["strict"] is True
        assert body["text"]["format"]["schema"]["additionalProperties"] is False
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(result)}]}], "usage": {"input_tokens": 100, "output_tokens": 50}})
    provider = OpenAIProvider("test-only-not-a-real-key", "test-model", httpx.MockTransport(handler))
    raw, usage = await provider.triage({"payload": request().model_dump()})
    assert Triage.model_validate(raw).category == "shipping"
    assert usage["tokens"]["input_tokens"] == 100 and usage["api_cost_usd"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status,response,code,retry", [
    (429, {"secret": "must-never-be-shown"}, "provider_http_429", True),
    (401, {"secret": "must-never-be-shown"}, "provider_http_401", False),
    (200, {"status": "incomplete"}, "provider_incomplete", False),
    (200, {"status": "completed", "output": [{"content": [{"type": "refusal"}]}]}, "provider_refusal", False),
    (200, {"status": "completed", "output": []}, "provider_invalid_response", False),
])
async def test_live_adapter_failure_contract(http_status, response, code, retry):
    provider = OpenAIProvider("test-only-not-a-real-key", "test-model", httpx.MockTransport(lambda _: httpx.Response(http_status, json=response)))
    with pytest.raises(ProviderError) as exc:
        await provider.triage({"payload": request().model_dump()})
    assert exc.value.code == code and exc.value.retryable is retry
    assert "must-never-be-shown" not in str(exc.value)


def test_metrics_include_more_than_the_display_limit(tmp_path):
    db = store(tmp_path)
    for i in range(103):
        db.create(request(), f"request-{i}")
    assert len(db.list()) == 100
    assert db.metrics() == {"total": 103, "by_status": {"queued": 103}}
