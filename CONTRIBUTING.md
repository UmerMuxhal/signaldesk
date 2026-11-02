# Contributing

The project license is pending. Discuss contributions with the maintainer before submitting code; no contributor agreement or support commitment is implied.

Keep this a small, local, single-worker demonstration. Preserve explicit sample/live labeling, the three-attempt provider budget, idempotency conflicts and the human-review boundary. Do not add external business actions to approval.

Use the README's dev installation and checks. Tests and smoke checks must use temporary databases, synthetic messages and mocked provider responses, without API keys. Add focused regression tests for behavior changes. Never run the direct HTTP smoke client against a personal development database.

When changing dependencies, update the direct declarations and constraints together, reinstall in a clean environment, run tests and inspect advisories. When adding configuration, update `.env.example` without copying real values. Do not include local logs, databases, generated exports, recordings or environment details in patches.

Before publication, inspect `git diff --cached`, `git ls-files`, reachable history and a redacted secret scan. Ignore patterns do not remove existing Git objects. Report reproduction steps with synthetic input; omit credentials and private provider responses. A license decision and maintainer review are required before presenting this as an open-source release.
