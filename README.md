# LLC — Laboratory Language Companion

<p align="center">
  <strong>Local-first language learning for laboratory and life-science professionals.</strong>
</p>

<p align="center">
  macOS · Local AI · Source-aware learning · Science-focused
</p>

LLC is a desktop-first language-learning system that combines deterministic
study workflows, a private educational library, document analysis, and an
optional locally hosted AI teacher. German is the current reference
implementation; the broader product is intended to support additional languages
and scientific domains.

LLC began as **DeutschOS**. That name is now retained only where it is
historically accurate or required for compatibility with existing local data.

> **Status:** active pre-release development. The current workflow targets macOS
> on Apple Silicon and is not yet distributed as a signed end-user release.

## What is implemented

- Deterministic daily plans, attempts, reviews, corrections, and skill evidence.
- Guided Study that remains available without a language model.
- A source-aware Educational Library with SQLite/FTS5 search, optional semantic
  retrieval, provenance, editorial state, and verified pedagogical memory.
- Document ingestion, version history, page-level comparison, structured
  extraction review, processing runs, audits, and canonical knowledge.
- A local AI teacher through LM Studio for chat, explanations, guided practice,
  and grounded library answers.
- A Next.js web interface and a native SwiftUI controller for macOS.
- Explicit local backup, migration, and privacy boundaries.

## Architecture

| Component               | Responsibility                                                                |
| ----------------------- | ----------------------------------------------------------------------------- |
| `apps/web`              | Next.js, React, and TypeScript interface                                      |
| `apps/api`              | FastAPI API, domain services, SQLAlchemy, and Alembic                         |
| Main SQLite database    | Learner profile, plans, attempts, reviews, and Study state                    |
| Library SQLite database | Rebuildable catalog, versions, chunks, embeddings, provenance, and audits     |
| LM Studio               | Optional local inference runtime                                              |
| Gemma models            | Configurable local generation roles for teacher, planning, vision, and repair |
| EmbeddingGemma          | Current default local embedding model for semantic retrieval                  |
| `apps/macos-controller` | Native SwiftUI lifecycle and service controller                               |

FastAPI is the authority for application data. The deterministic learning core,
profile, Study, catalog, and lexical search do not require LM Studio. Private
documents, model weights, runtime databases, logs, and generated artifacts stay
outside the tracked source tree.

See [architecture](docs/architecture.md), [data model](docs/data-model.md),
[security](docs/security.md), and [operations](docs/operations.md).

## Important execution boundaries

### Cloud knowledge extraction

Cloud extraction is an optional **ingestion-time** workflow. A request is made
only when a user explicitly executes a configured cloud extraction run. The
pipeline preserves provider output and normalizes it through:

```text
RAW → compact transport → canonical LLC artifact → local validation
```

It does not replace normal local ingestion, LM Studio, embeddings, retrieval, or
the teacher runtime. Gemini is currently the only real cloud adapter. See
[cloud knowledge extraction](docs/cloud-knowledge-extraction.md) and
[ADR 0019](docs/adr/0019-pluggable-cloud-knowledge-extraction.md).

### Graphify projection

Graphify support is an optional **canonical → graph projection**. LLC canonical
content remains the source of truth; projected graph artifacts are derived,
regenerable, and disposable. The adapter is disabled by default and imported
lazily.

Graphify is not connected to production teacher retrieval, and its output is not
accepted as canonical knowledge. See
[ADR 0020](docs/adr/0020-optional-graphify-projection.md).

## Requirements

- macOS on Apple Silicon for the complete desktop workflow;
- Node.js 20.9 or later;
- npm;
- Python 3.12;
- LM Studio only for local-model features.

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

Optional integrations are installed explicitly:

```bash
# Gemini cloud extraction
.venv/bin/python -m pip install -e 'apps/api[cloud]'

# Graphify projection and validation
.venv/bin/python -m pip install -e 'apps/api[graph]'
```

`.env` is private and ignored by Git. `LLC_*` names are canonical;
`DEUTSCHOS_*` configuration names exist only as transition fallbacks. The legacy
SQLite filename is intentionally preserved to avoid duplicating or destructively
migrating existing user data.

## Run locally

```bash
./scripts/doctor.sh
./scripts/dev.sh
```

Open <http://127.0.0.1:3000>. FastAPI documentation is available at
<http://127.0.0.1:8000/docs>.

`doctor.sh` checks runtimes, dependencies, SQLite, migrations, and local ports.
LM Studio being offline is a warning rather than a startup failure. `dev.sh`
starts FastAPI and Next.js on loopback; `Ctrl-C` stops both.

## macOS controller

Build the native controller without opening Xcode:

```bash
./scripts/build-macos-app.sh
open dist/LLC.app
```

The controller starts and stops the local services, reports their observed
state, and opens the LLC Safari Web App. Shell controls remain available:

```bash
./scripts/status.sh
./scripts/stop.sh
```

The generated app is locally signed and is not an official notarized release.

## Educational Library

Place private material in `material educativo/`. Original documents are not
modified; the rebuildable catalog and generated artifacts live under
`var/educational-library/`. Both locations are ignored by Git.

```bash
./scripts/educational-library.sh scan --metadata-only
./scripts/educational-library.sh scan
./scripts/educational-library.sh search "Akkusativ"
./scripts/educational-library.sh integrity
```

FTS5 lexical search works without a model. Semantic and hybrid retrieval use the
configured local embedding provider when available. LLC does not download model
weights automatically.

See [Educational Library](docs/educational-library.md),
[guided study](docs/guided-study.md), and
[pedagogical memory](docs/pedagogical-memory.md).

## Optional Graphify command

Project an existing `llc.cloud-content.v1` canonical JSON artifact without
changing it:

```bash
.venv/bin/python scripts/project-graphify.py path/to/canonical.json

LLC_GRAPH_ENABLED=true \
  .venv/bin/python scripts/project-graphify.py \
  path/to/canonical.json --validate-with-graphify
```

Outputs default to `var/educational-library/graphify-out/` and are ignored by
Git.

## Quality checks

```bash
.venv/bin/python -m pytest apps/api
.venv/bin/python -m ruff check apps/api scripts
.venv/bin/python -m ruff format --check apps/api scripts
./scripts/test-macos-app.sh
npm --workspace @llc/web test
npm run typecheck:web
npm run lint:web
npm run build:web
```

## Repository boundaries

Do not commit:

- `.env` files or API keys;
- copyrighted or private educational material;
- learner databases, library databases, or backups;
- model weights or LM Studio data;
- runtime logs, generated apps, or temporary graph/cloud artifacts;
- personal or machine-specific data.

Synthetic test PDFs under `apps/api/tests/fixtures/` are intentional tracked
fixtures. The actual private runtime areas are covered by `.gitignore`.

## Project and licensing status

The public repository is
[`Cuenca-john1999/laboratory-language-companion`](https://github.com/Cuenca-john1999/laboratory-language-companion).
Issues and well-scoped technical contributions are welcome, provided they
respect the repository boundaries above.

The repository does not currently contain a `LICENSE` file. Until one is added,
do not assume permission to copy, modify, or redistribute the source beyond the
rights granted by applicable law.

<p align="center">
  <strong>LLC</strong><br>
  <em>Learn the language. Work the science.</em>
</p>
