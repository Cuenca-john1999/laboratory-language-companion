# LLC — Laboratory Language Companion

<p align="center">
  <strong>Local-first language learning for laboratory and life-science professionals.</strong>
</p>

<p align="center">
  Desktop · Local AI · Open Source · Science-focused
</p>

LLC is an open-source, local-first desktop application for language learning
with a growing focus on laboratory, biomedical, and life-science work. It brings
together guided study, structured educational sources, professional vocabulary,
and an AI tutor that can run locally.

German is the current reference implementation used to validate the product and
architecture. It is not the identity of the whole application: LLC is designed
so that more language implementations and scientific domains can be added over
time.

LLC began as **DeutschOS**, a personal German-learning project. That name now
appears only where it is historically accurate or required for compatibility
with existing local data and installations.

## What LLC provides

- A deterministic learning engine for daily plans, attempts, reviews, and skill
  evidence.
- Study workflows that remain available without a language model.
- A source-aware educational library with lexical search, document versioning,
  structured review, and auditable provenance.
- A local AI teacher for explanations, guided practice, and grounded library
  answers when LM Studio is available.
- A Next.js interface and a native SwiftUI controller for macOS.
- Local SQLite storage with explicit backup, migration, and privacy boundaries.

Private educational material, study data, model weights, logs, and credentials
are not part of the public repository.

## Architecture

The current implementation uses:

- **Next.js and TypeScript** for the web interface;
- **FastAPI, SQLAlchemy, and Alembic** for the API and persistence layer;
- **SQLite and FTS5** for local application and educational-library data;
- **SwiftUI** for the native macOS controller;
- **LM Studio** as the optional local inference runtime.

The deterministic core does not depend on LM Studio. Profile, Study, Library,
daily planning, and evidence tracking continue to work while local AI is
offline.

For more detail, see [architecture](docs/architecture.md),
[data model](docs/data-model.md), [security](docs/security.md), and
[pedagogy](docs/pedagogy.md).

## Requirements

The current desktop workflow targets macOS on Apple Silicon and requires:

- Node.js 20.9 or later;
- npm;
- Python 3.12;
- LM Studio only for features that require local inference.

With Homebrew:

```bash
brew install node python@3.12 lm_studio
```

## Install

From the repository root:

```bash
test -f .env || cp .env.example .env
npm ci
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e 'apps/api[dev]'
```

`.env` is private and excluded from Git. New configuration uses the `LLC_*`
prefix. Equivalent `DEUTSCHOS_*` names are accepted only as transition
fallbacks, with `LLC_*` taking precedence. The legacy SQLite filename remains
unchanged to avoid a destructive or duplicate data migration.

## Run locally

```bash
./scripts/doctor.sh
./scripts/dev.sh
```

Open <http://127.0.0.1:3000>. API documentation is available at
<http://127.0.0.1:8000/docs>.

`doctor.sh` checks local runtimes, dependencies, SQLite, migrations, and ports.
LM Studio being unavailable is reported as a warning rather than an essential
failure. `dev.sh` starts FastAPI and Next.js on loopback; `Ctrl-C` stops both.

Operational details, backups, launch behavior, and troubleshooting are covered
in [operations](docs/operations.md).

## macOS controller

Build the native controller without opening Xcode:

```bash
./scripts/build-macos-app.sh
open dist/LLC.app
```

The build creates `dist/LLC.app`. The controller starts and stops the local
services, reports their real state, and opens the installed LLC Safari Web App.
An existing `~/Applications/DeutschOS.app` can still be detected as a legacy
fallback; it is never copied, recreated, or deleted.

Shell controls remain available:

```bash
./scripts/status.sh
./scripts/stop.sh
```

## Main modules

- **Dashboard and Study** — current plan, guided missions, attempts, and
  deterministic corrections.
- **Learning Engine** — curriculum, reviews, skill estimates, and append-only
  evidence.
- **Teacher** — local-model conversations and language support.
- **Library** — private educational sources, search, lifecycle, and provenance.
- **Document Laboratory** — comparisons, extraction review, runs, audits, and
  readiness inspection.
- **Data and memory** — local state and verified pedagogical memory.

See [Learning Engine](docs/learning-engine.md),
[guided study](docs/guided-study.md),
[educational library](docs/educational-library.md), and
[pedagogical memory](docs/pedagogical-memory.md).

## Repository structure

- `apps/web` — Next.js application and UI tests.
- `apps/api` — FastAPI application, migrations, and Python tests.
- `apps/macos-controller` — native SwiftUI controller.
- `packages/shared` — shared TypeScript contracts.
- `docs` — architecture, pedagogy, security, operations, and ADRs.
- `scripts` — development, diagnostics, controller, and backup tooling.
- `data`, `material educativo`, and `var/educational-library` — private local
  runtime areas excluded from public source control.

## Quality checks

```bash
.venv/bin/python -m pytest apps/api
.venv/bin/python -m ruff check apps/api scripts
.venv/bin/python -m ruff format --check apps/api scripts
./scripts/test-macos-app.sh
npm --workspace @llc/web test
npm --workspace @llc/web run typecheck
npm run lint:web
npm run build:web
```

The macOS application can be packaged separately with
`./scripts/build-macos-app.sh`.

## Project status

LLC is in active, pre-release development. German is functional as the first
implementation while the architecture is progressively separated into reusable
language-learning and scientific-domain capabilities. The application is
desktop-first; mobile support is not currently a project goal.

The public repository is
[`Cuenca-john1999/laboratory-language-companion`](https://github.com/Cuenca-john1999/laboratory-language-companion).
Bug reports, technical discussion, and well-scoped contributions are welcome,
provided they do not include copyrighted learning material, private databases,
credentials, model files, or personal logs.

<p align="center">
  <strong>LLC</strong><br>
  <em>Learn the language. Work the science.</em>
</p>
