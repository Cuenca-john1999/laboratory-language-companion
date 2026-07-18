# ADR 0010: Verified pedagogical memory

- Status: Accepted
- Date: 2026-07-18

## Context

Teacher queries already persist generated answers and the original chunks used as
evidence. That history is useful for replay, but it is not a safe knowledge base:
an answer produced by a local model can be incomplete, and a PDF page number can
refer to a double-page scan rather than to the number printed in the book.

DeutschOS needs to remember concepts and verified document locations without
promoting generated prose to truth or contaminating normal learner progress.

## Decision

The library SQLite schema 4 adds a separate pedagogical-memory layer:

- stable concepts, multilingual aliases and reviewable relations;
- evidence locations tied to a source version and, when available, an original
  chunk or editorial section;
- a versioned PDF-page mapping that separates zero-based processing index,
  one-based public PDF page, printed labels, scan layout and region;
- the states `candidate`, `system_verified`, `user_confirmed`, `rejected`,
  `conflict` and `stale`;
- independent response and location feedback;
- append-only reviews and audit events with reversible state projections;
- query-to-concept and query-to-location links recording what memory was used.

Trust is ordered as human confirmation, limited deterministic verification and
candidate evidence. A model may propose a candidate only after a completed,
grounded query. It cannot create `system_verified` or `user_confirmed` knowledge.
An answer marked correct does not confirm its citations. A location marked
incorrect creates an exact negative for concept, source version, page/region and
evidence; it does not invalidate the whole page for other concepts.

The Teacher retrieval flow detects relevant aliases, consults current confirmed
and system-verified locations before normal hybrid retrieval, then verifies the
original chunks. Candidate locations are only a secondary boost. Rejected and
stale locations are excluded. Relevance remains mandatory.

Memory stays in the dedicated library database. It does not write `StudentSkill`,
`SkillEvidence`, curriculum, diagnostic results or the Learning Engine.

## Consequences

- A source rename with unchanged content keeps the source/version identity and
  therefore keeps memory valid.
- A new content version marks earlier locations and page mappings stale.
- Printed page labels remain strings and may be Roman numerals, ranges or unknown.
- Public citations can explain PDF and printed pagination without exposing paths,
  hashes or internal processing indices.
- Historical corrections remain auditable instead of being deleted.
- Schema 3 is backed up, checked and atomically published under the library
  runtime `backups/` directory before the transactional schema 4 upgrade.

The cost is additional editorial state and a review queue. Confirmed memory still
cannot replace retrieval from the original evidence, and “no lo sé” deliberately
does not increase or decrease trust.

## Alternatives considered

- **Cache complete generated answers.** Rejected because it turns model output
  into a fragile source of truth and makes corrections hard to scope.
- **Store page notes inside source metadata.** Rejected because it cannot model
  concepts, exact negative evidence, double-page regions or audit history.
- **Use a second database.** Rejected because source-version and chunk integrity
  would become harder to enforce transactionally.
- **Automatically confirm every cited location.** Rejected because a grounded
  answer can still cite a nearby or ambiguous passage.

## Revisit when

- automatic visual page-region detection is reliable enough for bounded review;
- multiple users require separate identities and permissions;
- a reviewed concept ontology needs controlled category migrations;
- pedagogical-memory evidence is explicitly approved for projection into normal
  learner progress through a separate ADR.
