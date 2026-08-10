# ADR 0017: Product identity migration from DeutschOS to LLC

- Status: Accepted
- Date: 2026-08-10

## Context

The product began as DeutschOS, a German-learning application. Its scope now
includes language learning for laboratory, biomedical, and life-science
professionals, with German as the first implementation rather than the product
identity. The current public name is **LLC — Laboratory Language Companion** and
the public repository slug is `laboratory-language-companion`.

The rename must not rewrite Git history, mutate educational provenance, or
force migrations of existing user data merely for branding.

## Decision

- Current UI, API metadata, logs, launcher messages, and documentation use LLC.
- The JavaScript root package is `laboratory-language-companion`; workspace
  packages use `@llc/*`.
- The internal Python package is `llc_api`. It was migrated atomically because
  repository inspection found no persisted module references or external plugin
  contract requiring an import shim.
- New configuration uses `LLC_*`. Equivalent `DEUTSCHOS_*` variables remain a
  temporary fallback, with explicit `LLC_*` precedence.
- The native product and display name is `LLC.app`. The controller continues to
  recognize an installed `DeutschOS.app` Safari Web App as a legacy fallback and
  never deletes or duplicates it.
- The controller keeps the bundle identifier `local.deutschos.controller`.
  Changing it would create a new application identity for macOS preferences,
  permissions, signing, and process discovery; no established replacement
  reverse-domain convention exists yet.
- The Safari Web App bundle identifier is retained because it identifies the
  user's existing installed web app. Display/path lookup prefers `LLC.app` and
  falls back to `DeutschOS.app`.
- Runtime paths are derived from the repository root. The repository directory
  itself is not renamed by this change.
- Existing runtime data is not moved. `data/deutschos.sqlite3`, historical backup
  names, and existing runtime directories remain valid. New backup bundles use
  the `llc-` prefix.
- Database tables, source IDs, version IDs, hashes, UUID namespaces, diagnostic
  bank IDs, audit package schema fields, and `deutschos-status-v1` remain legacy
  persistent or compatibility identifiers. Renaming them would add risk without
  a functional benefit.
- Historical migrations, ADRs, audit packages, snapshots, and commit history are
  not rewritten. DeutschOS remains accurate when describing project history.
- Functional URLs such as `/api/library`, `/study`, `/laboratory`, `/data`, and
  `/chat` remain unchanged.

## Filesystem and database policy

New code must discover the repository root or use relative/configured paths; it
must not hardcode a developer-specific clone location. An installation may keep
legacy data paths indefinitely. A future physical data migration requires its
own reversible, capacity-aware design and is outside this identity change.

Branding alone never justifies renaming SQLite tables, stored identifiers, or
educational-library artifacts. The application must open the existing databases
without a branding migration.

## Repository policy

The public repository is
`Cuenca-john1999/laboratory-language-companion`. Existing DeutschOS commits are
preserved as ancestry. Integration must use a normal non-destructive Git merge
or cherry-pick and must not use history rewriting or a force push.

## Consequences

The visible application consistently presents LLC while a small, audited set of
legacy identifiers remains. Those identifiers are compatibility debt, not
current branding, and every remaining occurrence must be classifiable as
historical, persistent, migration-related, or a transition fallback.
