"""Exercise a running sample server. Creates five synthetic records; never calls a model."""
import argparse
import time
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=10, trust_env=False) as client:
        deadline = time.monotonic() + 15
        while True:
            try:
                health = client.get("/health")
                if health.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Sample server did not become healthy.")
            time.sleep(0.1)
        assert health.json() == {"status": "ok", "mode": "sample"}
        assert client.get("/api/config").json()["mode"] == "sample", "Sample mode required"
        for path in ("/", "/app.js", "/style.css", "/openapi.json"):
            assert client.get(path).status_code == 200
        for scenario, expected, attempts in (("normal", "needs_review", 1),
                ("transient", "needs_review", 2), ("permanent", "failed", 3),
                ("invalid_output", "failed", 1), ("refusal", "failed", 1)):
            body = {"message": "Synthetic check: order 1042 delivery is delayed.", "scenario": scenario}
            headers = {"Idempotency-Key": str(uuid4())}
            first = client.post("/api/jobs", json=body, headers=headers)
            assert first.status_code == 202
            job_id = first.json()["id"]
            duplicate = client.post("/api/jobs", json=body, headers=headers).json()
            assert duplicate["deduplicated"] and duplicate["id"] == job_id
            assert client.post("/api/jobs", json={**body, "message": "Different synthetic request."},
                               headers=headers).status_code == 409
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                job = client.get("/api/jobs/" + job_id).json()
                if job["status"] in ("needs_review", "failed"):
                    break
                time.sleep(0.05)
            assert (job["status"], job["attempt"]) == (expected, attempts), job
            assert job["events"][0]["kind"] == "queued"
            if expected == "needs_review":
                result = {**job["result"], "summary": "Reviewer confirmed this synthetic summary."}
                approved = client.post(f"/api/jobs/{job_id}/approve", json={"result": result})
                assert approved.status_code == 200
                exported = client.get("/api/jobs/" + job_id).json()
                assert exported["approved_result"] == result
                assert exported["result"] == job["result"]
                assert exported["events"][-1]["kind"] == "approved"
            else:
                assert job["result"] is None
            print(f"PASS {scenario}: {expected}, {attempts} attempt(s)")
    print("HTTP health, assets, idempotency, conflicts, processing, review and export data passed.")


if __name__ == "__main__":
    main()
