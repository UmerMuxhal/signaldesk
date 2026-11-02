# SignalDesk

A personal FastAPI demonstration of the engineering around an AI-assisted support workflow: persist a request, process it in the background, validate the result, and let a person review it.

**SAMPLE MODE uses deterministic keyword rules, not a language model. No API key is needed.** The queue, retries, validation, saved review decisions and JSON export are implemented behavior. This is a demonstration using synthetic data, not a production-certified product or a client case study.

## Workflow

`request → persisted job → worker → validated result → human review → saved approval`

- SQLite persistence retains jobs and event history across restarts.
- An idempotency key returns the same job for the same input; different input with that key returns HTTP 409.
- One worker processes jobs with bounded backoff and a maximum of three provider attempts, including interrupted attempts.
- A per-database process lock prevents a second local worker from recovering active jobs.
- Invalid output, incomplete responses and refusals cannot reach review.
- Approval saves reviewer edits alongside the original result. It sends no email and performs no refund, payment or CRM action.
- Export JSON downloads the selected saved record and event history. Counters cover all records; the inbox shows the latest 100.

## Quick start

Use Python **3.11–3.13** and Node.js 24 for the optional JavaScript syntax check. Initial Python installation needs network access; sample processing does not.

```powershell
git clone git@github.com:UmerMuxhal/signaldesk.git
cd signaldesk
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_demo.py
```

Open the URL printed by the launcher, normally **http://127.0.0.1:8000**. It binds only to localhost, uses one worker, and tries the next 19 ports if the requested port is occupied. It refuses a second active worker on the same database.

On Windows, `.\start-demo.cmd` performs the same environment bootstrap. On macOS/Linux use `sh start-demo.sh`, or substitute `.venv/bin/python` for the Windows Python path above. No activation is required.

Stop with Ctrl+C in the server terminal, or from another PowerShell window:

```powershell
.\.venv\Scripts\python.exe run_demo.py --stop --port 8000
```

Use the actual printed port. The launcher lets the current bounded attempt finish; pending jobs remain persisted. The stop command applies to `run_demo.py`, not directly launched Uvicorn or Docker.

## Configuration

[.env.example](.env.example) documents safe defaults. **The app does not automatically load .env files.** Set variables in the terminal before launching:

| Variable | Default / meaning |
|---|---|
| `PROOFKIT_MODE` | `sample`; only `sample` and `live` are accepted, including no blank value |
| `PROOFKIT_DB` | `data/jobs.sqlite3` beside `app.py`; use a persistent SQLite file, not an empty path or `:memory:` |
| `OPENAI_API_KEY` | Required only for live mode; kept server-side |
| `OPENAI_MODEL` | Required only for live mode; choose a model your account supports with Responses structured outputs |

For a separate recording dataset on Windows:

```powershell
$env:PROOFKIT_MODE = "sample"
$env:PROOFKIT_DB = "data/recording.sqlite3"
.\.venv\Scripts\python.exe run_demo.py
```

Use the same database environment when issuing `--stop`. Relative paths use the current working directory; launcher control state is stored in a `runtime/` directory beside the database. POSIX shells use `export PROOFKIT_MODE=sample` instead of `$env:PROOFKIT_MODE = "sample"`. Preserve existing data when changing configuration.

Live mode is optional and costs money. Set all three live variables privately in your terminal; do not put credentials in files, browser code, screenshots or issue reports. It sends the entered text to the official OpenAI Responses endpoint with a strict schema and `store: false`. Failure injection is disabled in live mode. Token counts are saved; monetary cost is left unknown. Mocked adapter tests do not establish live-model correctness. Schema validation does not establish factual accuracy, and `store: false` is not a blanket data-retention guarantee. See [OpenAI's Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

## Two-minute walkthrough

1. Point out **SAMPLE MODE · NO API CALLS**.
2. Choose **Delayed order**, keep normal processing, and queue it. Inspect its processing trail and validated fields.
3. Edit the summary and approve. Explain that this saves a reviewed record without contacting a customer.
4. Choose **Duplicate charge** with the transient failure scenario. Inspect attempt one failing and attempt two recovering.
5. Resend with the same key: the existing job returns without increasing the total.
6. Submit the invalid-output scenario: processing fails and approval is unavailable. Select the approved record and export JSON.

Unsaved review drafts survive polling and switching requests within the tab. Reloading discards unsaved drafts; saved approvals and selected job are retained.

## Tests and validation

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app.py run_demo.py tests
node --check static/app.js
.\.venv\Scripts\python.exe tests/run_smoke.py
```

The smoke runner starts sample mode with a temporary database, exercises all scenarios through HTTP, stops cleanly and checks persistence after restarting. It does not modify your development database. Directly running `tests/http_smoke.py --base-url URL` adds five synthetic records to that server; use only an isolated sample server.

Direct dependencies are pinned in `requirements*.txt`; `constraints.txt` pins the tested transitive versions. Keep them together when installing. See [VERIFICATION.md](VERIFICATION.md) for actual checks and their limits. CI runs tests and smoke checks on Linux and Windows with synthetic data; a workflow file alone is not evidence of a successful run.

## Docker

```powershell
docker build -t signaldesk-local .
docker run -d --name signaldesk-demo -p 127.0.0.1:8000:8000 -v signaldesk-demo-data:/app/data signaldesk-local
docker stop -t 40 signaldesk-demo
docker start signaldesk-demo
```

Publish to host loopback as shown. Use another host port if 8000 is occupied. The image runs as a non-root user with one worker. The named volume retains records when the container stops. Use only one container per database; cross-container/shared-network-filesystem locking is not a supported architecture. The image contains only the application, dependency declarations and static source, without local records or credentials.

## Architecture and limits

`app.py` owns the FastAPI routes, SQLite store, worker, schema models and sample/live providers. `static/` contains the browser interface. `run_demo.py` handles local startup/stop; `tests/` uses temporary state and mocked provider responses. `GET /health` checks SQLite access; `/openapi.json` documents the API.

This local, single-user demonstration has no authentication, tenant isolation, distributed queue or public-deployment safeguards. Host/origin checks do not replace authentication. Use synthetic requests and keep it off public interfaces. A crash after a provider call but before saving its result can repeat the call; idempotent submission is not exactly-once execution or billing. No live-model accuracy, cost, latency, scalability or production-readiness claim is made.

## Author and licensing

**Umer Mughal — Senior Python Backend Engineer**

[Portfolio](https://umer.fuhzion.com/) · [SignalDesk case study](https://umer.fuhzion.com/projects/signaldesk) · [LinkedIn](https://www.linkedin.com/in/umermuxhal/)

The case study is portfolio material, not a hosted SignalDesk application. Repository visibility is managed separately from this demonstration. Licensed under the [MIT License](LICENSE). Copyright (c) 2026 Umer Mughal. No third-party fonts, images or media are bundled; runtime dependencies keep their respective licenses.
