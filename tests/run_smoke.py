"""Run real HTTP checks and restart recovery with isolated, synthetic sample data."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def sample_server(database, log):
    env = {**os.environ, "PROOFKIT_MODE": "sample", "PROOFKIT_DB": str(database),
           "PYTHONIOENCODING": "utf-8"}
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen([sys.executable, "run_demo.py"], cwd=ROOT, env=env,
                                   stdout=output, stderr=subprocess.STDOUT)
        url = None
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Isolated sample server exited during startup.")
                match = re.search(r"http://127\.0\.0\.1:(\d+)", log.read_text(encoding="utf-8"))
                if match:
                    url = match.group(0)
                    try:
                        if httpx.get(url + "/health", timeout=1, trust_env=False).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                time.sleep(0.05)
            else:
                raise RuntimeError("Isolated sample server did not become healthy.")
            yield url
        finally:
            if process.poll() is None and url:
                subprocess.run([sys.executable, "run_demo.py", "--stop", "--port", url.rsplit(":", 1)[1]],
                               cwd=ROOT, env=env, check=True, capture_output=True, timeout=5)
                try:
                    process.wait(timeout=40)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
                    raise
            elif process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            if process.returncode != 0:
                raise RuntimeError("Isolated sample server did not exit cleanly.")


def main():
    with TemporaryDirectory(prefix="signaldesk-smoke-") as directory:
        temporary = Path(directory)
        database = temporary / "jobs.sqlite3"
        with sample_server(database, temporary / "first.log") as url:
            subprocess.run([sys.executable, "tests/http_smoke.py", "--base-url", url],
                           cwd=ROOT, check=True, timeout=40)
            original = httpx.get(url + "/api/jobs", trust_env=False).json()
        with sample_server(database, temporary / "restart.log") as url:
            reopened = httpx.get(url + "/api/jobs", trust_env=False).json()
            assert {j["id"] for j in original} == {j["id"] for j in reopened}
            assert len(reopened) == 5
            assert sum(j["status"] == "approved" for j in reopened) == 2
    print("PASS isolated startup, HTTP workflow, graceful shutdown and persisted restart")


if __name__ == "__main__":
    main()
