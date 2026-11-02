# Release-candidate verification

Review date: 3 November 2026. This record concerns the source prepared for the private repository, not a public release or a production certification.

## Scope and publication review

The input folder had no Git repository, reachable history, submodules or nested repositories. A new main branch was initialized in place; no previous history was rewritten and no separate history-cleaning export was needed. GitHub SSH enumeration and the signed-in repository page confirmed the target was empty and private before preparation.

Local databases, logs, exports, screenshots, tool environments, private proposal notes and old machine-specific verification notes are excluded. Original local notes and data were preserved. The distributable tree contains source, synthetic tests and project documentation; it bundles no media or third-party visual assets. The safe `.env.example` contains no credentials. No live credential was identified in the distributable content; scanner results are a limited check, not proof that no secret or vulnerability exists.

## Checks

A fresh Windows Python 3.13 environment installed the pinned runtime/dev dependencies successfully. Results:

| Command / check | Result |
|---|---|
| `python -m pip install -r requirements-dev.txt` | Clean install passed |
| `python -m pip check` | No broken requirements |
| `python -m pytest -q` | **41 passed in 13.80 seconds** |
| `python -m compileall -q app.py run_demo.py tests` | Passed |
| `node --check static/app.js` (Node 24) | Passed |
| `python tests/run_smoke.py` | Five HTTP scenarios, review/export data, graceful stop and persisted restart passed |
| `pip-audit -r requirements-dev.txt` (2.10.1) | No known vulnerabilities in the pinned dependency set |
| Gitleaks 8.30.1, redacted directory scan | No leaks found in the reviewed source tree |
| Targeted source review | No credential-shaped values, machine paths or unintended large files found in distributable files |
| `docker build -t signaldesk-release-review .` | Clean-context build passed; Python 3.12, non-root runtime |
| `python tests/http_smoke.py --base-url http://127.0.0.1:8010` | All five Docker scenarios passed |
| Docker stop/start/restart | Health ok and five synthetic records retained; disposable review container removed |

The suite emits one upstream Starlette TestClient/httpx deprecation warning. Dependency versions were retained rather than changing frameworks to suppress the warning. Test duration is not an application-performance benchmark. Scanner versions and findings are review-time observations, not continuous security guarantees.

The clean candidate was assembled from Git's non-ignored source list into an ignored local validation directory, with a fresh virtual environment and temporary test databases. It contains no original `.git` or development database.

Direct dependency versions are retained. `constraints.txt` pins the previously tested transitive set. No lint or type-checker configuration existed; Python compilation and JavaScript syntax are the existing static checks.

## Behavioral coverage

Tests cover persisted requests, idempotency conflicts, retry/backoff and attempt limits, repeated crash recovery, exclusive worker ownership, schema rejection, malformed/incomplete/refused provider responses, saved reviewer edits alongside original output, approval eligibility, metrics beyond 100 rows, explicit live configuration, blank/invalid settings, actual launcher port fallback and graceful shutdown. The isolated HTTP smoke runner exercises all five synthetic scenarios, approval and exported event data, then restarts against the same temporary database.

Sample behavior is deterministic keyword logic. OpenAI responses are mocked; no paid API calls or model evaluations are made. Schema validity does not imply factual accuracy. Approval only saves data in the demonstration.

## Limitations and release gate

At the initial release-candidate review, no project license had been selected. On 3 November 2026, following explicit author approval, the [MIT License](LICENSE) was added with Copyright (c) 2026 Umer Mughal. The licensing decision is complete; this does not constitute a public release. The repository must remain private pending review; no visibility changes, deployment, package publication or public release are part of this work.

There is no authentication, tenant isolation or production deployment hardening. Distributed/shared-filesystem database use is unsupported. Physical mobile devices, comprehensive accessibility, live provider/model behavior and production load are outside this review. Earlier desktop/mobile browser checks are local demonstration evidence; media are intentionally excluded from the repository.

The `.github/workflows/checks.yml` workflow targets main with read-only contents permission, no persisted checkout credentials or production secrets, and actions pinned to official release SHAs. Version sources checked: [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1), [setup-python v7.1.0](https://github.com/actions/setup-python/releases/tag/v7.1.0), [setup-node v7.1.0](https://github.com/actions/setup-node/releases/tag/v7.1.0). A workflow file is not evidence of a completed CI run; final CI status is reported separately after pushing.
