# Cloud Knowledge Extraction (#009ING4)

## Scope and architecture

Cloud extraction is an explicit, optional extension of the educational-library pipeline. It does
not replace local ingestion, LM Studio, the tutor, chunks, embeddings, or canonical-route review.
Every extraction pins an existing `source_version` and reuses `document_processing_runs` for its
state machine and audit trail.

The boundary is intentionally three-layered:

1. **Provider response**: the provider adapter returns an unvalidated RAW envelope. LLC writes it
   to the private library runtime immediately.
2. **Compact transport**: provider-neutral, token-conscious JSON for either `structure` or
   `content`. This is validated locally and is never treated as LLC's durable domain format.
3. **Canonical LLC artifact**: LLC assigns IDs, schema/provenance metadata, separates immutable
   `raw_visible_text` from derived labels, and validates evidence and relationships.

The stages are `provider_request`, `transport_validation`, `canonical_normalization`, and
`local_validation`. RAW, transport, canonical, and validation artifacts live under
`var/educational-library/cloud-knowledge/<run-id>/`; SQLite stores their hashes, sizes, relative
paths, provider/model/mode, prompt/schema versions, page scope, usage, normalized errors, and run
status. Schema migration 14 is additive and creates a verified schema-13 backup before migrating
an existing library.

Validation reports use `pass`, `warn`, or `fail`. Warnings preserve useful output. Extracted
knowledge and relations require locating evidence; model deductions remain `inferred`. Later
human review can mark either `verified` or `rejected` without erasing the extractor's original
status.

## Gemini configuration

Install the optional official SDK and PDF page-selection dependency without changing the default
local runtime:

```bash
.venv/bin/python -m pip install -e 'apps/api[cloud]'
```

Put the key in the untracked repository `.env` or inject it into the process environment:

```bash
export GEMINI_API_KEY='your-key-from-a-secret-manager'
export LLC_CLOUD_KNOWLEDGE_GEMINI_MODEL='gemini-2.5-flash'
```

Never add the real key to `.env.example`, source code, commands saved in scripts, logs, or commits.
`GEMINI_API_KEY` and `LLC_GEMINI_API_KEY` are accepted; Pydantic stores the value as `SecretStr`.
The model is independently configurable. `generateContent` is stateless; uploaded PDF files are
deleted in `finally`, and a cleanup failure becomes a diagnostic warning without discarding a
valid response.

Cloud data is sent only by `POST /api/library/cloud-knowledge/runs/{run_id}/execute` or the
explicit smoke command. Creating a run, listing providers, normal ingestion, and application
startup make no cloud request.

## API

- `GET /api/library/cloud-knowledge/providers`: installed providers, configuration state, model,
  availability, and capabilities.
- `POST /api/library/cloud-knowledge/runs`: create a planned version-pinned run with provider,
  model, mode, and optional one-based physical PDF pages.
- `POST /api/library/cloud-knowledge/runs/{id}/execute`: perform the explicit network action.
- `GET /api/library/cloud-knowledge/runs/{id}`: status, provenance, usage, normalized errors, and
  artifacts.
- `GET /api/library/cloud-knowledge/runs/{id}/validation`: local validation report.

`provider: "auto"` currently selects the sole configured compatible provider. A manual provider
can be selected in the request or with `LLC_CLOUD_KNOWLEDGE_PROVIDER`. There is no invented
fallback or hardcoded quota threshold.

## Tests and live smoke

Normal tests are offline and use the provider interface fake:

```bash
.venv/bin/python -m pytest -q apps/api/tests/test_cloud_knowledge.py
```

After registering the PDF through the existing educational library, find its version ID and run
one deliberate STRUCTURE smoke over the Herder index pages:

```bash
.venv/bin/python scripts/cloud-knowledge-smoke.py --live --source-version-id VERSION_ID --mode structure --pages 1-17
```

Omitting `--live` or `GEMINI_API_KEY` exits before network access. The copyrighted PDF remains in
the user-owned educational library and is not copied into the repository.

## Adding provider B

Implement `CloudKnowledgeProvider` in `cloud_knowledge/providers/`, expose a descriptor with
capabilities, translate native failures to `ProviderAvailability`, and return a
`ProviderRawResponse`. Register it in `get_cloud_provider_registry`. Do not change transport
models, canonical models, database tables, normalizers, validators, retrieval, or tutor code.
A future routing policy can order compatible registry results and use normalized availability;
the current core contains no provider-specific quota logic.

## Current limits

- Gemini is the only real adapter; AUTO has no cross-provider fallback yet.
- Runs process registered PDFs. When a run selects fewer than all pages, the Gemini adapter creates
  a private local temporary PDF containing only those pages, uploads it, and deletes it in
  `finally`. Full-document runs upload the registered source directly.
- Execution is synchronous at the service/API boundary; it is represented by durable processing
  jobs but no distributed worker is introduced in this milestone.
- Graphify, a knowledge-graph runtime, automatic ingestion, billing dashboards, and dual-provider
  discrepancy review are intentionally out of scope.
