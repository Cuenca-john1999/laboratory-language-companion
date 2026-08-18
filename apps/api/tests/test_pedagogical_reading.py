from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_document_review import review_library as review_library_fixture  # noqa: E402

from llc_api.educational_library.dependencies import (  # noqa: E402
    get_library_model_router,
    get_pedagogical_reading,
)
from llc_api.educational_library.reading import (  # noqa: E402
    PedagogicalReadingOutput,
    PedagogicalReadingService,
    ReadingRunCreate,
)
from llc_api.educational_library.review import DocumentReviewService  # noqa: E402
from llc_api.educational_library.service import EducationalLibraryService  # noqa: E402
from llc_api.main import app  # noqa: E402


class FixtureRouter:
    def __init__(self, *, fail_topics: set[int] | None = None, unresolved: bool = False):
        self.fail_topics = fail_topics or set()
        self.unresolved = unresolved
        self.calls: list[dict[str, Any]] = []

    async def select(self, role):
        del role
        return "fixture-model"

    async def structured_generate(self, role, messages, schema, *, interactive=True):
        payload = json.loads(messages[1]["content"])
        topic = int(payload["topic"])
        self.calls.append({"topic": topic, "interactive": interactive, "role": role})
        if topic in self.fail_topics:
            raise RuntimeError("fixture empty or truncated model output")
        block = payload["evidence"][0]
        result = schema.model_validate(
            {
                "topic": topic,
                "evidence_scope": [block["evidence_id"]],
                "candidates": [
                    {
                        "local_id": f"topic-{topic}-definition",
                        "candidate_type": "definition",
                        "content": {"statement": f"Observed definition for topic {topic}"},
                        "terminology_de": ["Begriff"],
                        "terminology_es": [],
                        "observed_level": None,
                        "evidence": [{"block_id": block["evidence_id"], "quote": block["text"]}],
                        "confidence": 0.8,
                        "uncertainty": None,
                    }
                ],
                "relations": [],
                "unresolved": ["Needs another observed example"] if self.unresolved else [],
                "warnings": [],
                "conflicts": [],
            }
        )
        return result, "fixture-model", False


class SequenceRouter:
    def __init__(self, outputs: list[dict[str, Any]]):
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    async def structured_generate(self, role, messages, schema, *, interactive=True):
        self.calls.append(
            {"role": role, "messages": messages, "interactive": interactive}
        )
        return schema.model_validate(self.outputs.pop(0)), "fixture-model", False


@pytest.fixture
def reading_library(tmp_path: Path):
    database, version_id = review_library_fixture.__wrapped__(tmp_path)
    review = DocumentReviewService(database).consolidate(version_id)
    now = "2026-08-10T10:00:00+00:00"
    with database.transaction(immediate=True) as connection:
        candidates = connection.execute(
            "SELECT DISTINCT c.id,c.page_id,c.raw_text,c.normalized_layout_text,c.reading_order "
            "FROM document_consolidated_nodes n JOIN document_structure_candidates c "
            "ON c.id=n.source_candidate_id WHERE n.source_version_id=? AND n.theme_number IS NOT NULL "
            "AND coalesce(c.raw_text,c.normalized_layout_text,'')<>'' ORDER BY c.page_id,c.reading_order",
            (version_id,),
        ).fetchall()
        for index, candidate in enumerate(candidates):
            block_id = f"reading-block-{candidate['id']}"
            connection.execute(
                "INSERT INTO document_page_blocks(id,source_id,source_version_id,page_id,run_id,stage,"
                "block_index,block_type,raw_text,normalized_layout_text,reading_order,confidence,"
                "evidence_json,issues_json,extractor,extractor_version,created_at,updated_at) "
                "VALUES (?,'review',?,?,'review-run','fixture',?,'text',?,?,?,1,'{}','[]',"
                "'fixture','fixture.v1',?,?)",
                (
                    block_id,
                    version_id,
                    candidate["page_id"],
                    index,
                    candidate["raw_text"],
                    candidate["normalized_layout_text"],
                    candidate["reading_order"],
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE document_structure_candidates SET block_id=? WHERE id=?",
                (block_id, candidate["id"]),
            )
        version = connection.execute(
            "SELECT content_hash FROM source_versions WHERE id=?", (version_id,)
        ).fetchone()
        payload = json.dumps({"fixture": True}, sort_keys=True)
        connection.execute(
            "INSERT INTO document_closure_snapshots(id,source_id,source_version_id,document_hash,"
            "review_run_id,structural_readiness,ai_readiness,state,topic_revision,hierarchy_revision,"
            "review_revision,relation_revision,coverage_json,unresolved_json,blocking_json,"
            "pipeline_versions_json,snapshot_payload_json,snapshot_hash,created_at) "
            "VALUES ('reading-closure','review',?,?,?,?,'ready_for_ai_with_issues','ready_with_issues',"
            "'fixture','fixture','fixture','fixture','{}','[]','[]','{}',?,?,?)",
            (
                version_id,
                version["content_hash"],
                review["id"],
                "structurally_ready_with_issues",
                payload,
                hashlib.sha256(payload.encode()).hexdigest(),
                now,
            ),
        )
    return database, version_id


def _create(service: PedagogicalReadingService, version_id: int) -> dict[str, Any]:
    return service.create_run(
        ReadingRunCreate(source_version_id=version_id, max_passes=3),
        resolved_model="fixture-model",
    )


def _create_automatic(service: PedagogicalReadingService, version_id: int) -> dict[str, Any]:
    return service.create_run(
        ReadingRunCreate(source_version_id=version_id, max_passes=3, auto_continue=True),
        resolved_model="fixture-model",
    )


def _only_first_topic(database, run_id: str) -> None:
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE pedagogical_reading_topic_stages SET state='skipped' "
            "WHERE run_id=? AND topic_number>1",
            (run_id,),
        )


def _candidate_output(database, stage: dict[str, Any]) -> dict[str, Any]:
    block_id = stage["block_scope"][0]
    with database.connect() as connection:
        quote = connection.execute(
            "SELECT raw_text FROM document_page_blocks WHERE id=?", (block_id,)
        ).fetchone()[0]
    return {
        "topic": 1,
        "evidence_scope": [block_id],
        "candidates": [
            {
                "local_id": "candidate-1",
                "candidate_type": "definition",
                "content": {"statement": "Synthetic low-volume observation"},
                "evidence": [{"block_id": block_id, "quote": quote}],
                "confidence": 0.5,
            }
        ],
    }


def test_schema_13_round_trip_preserves_existing_library_rows(reading_library):
    database, _ = reading_library
    with database.connect() as connection:
        before = tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM sources),(SELECT count(*) FROM source_versions),"
                "(SELECT count(*) FROM document_structure_candidates),"
                "(SELECT count(*) FROM document_consolidated_topics)"
            ).fetchone()
        )
    assert database.rollback_version_14() == 13
    assert database.rollback_version_13() == 12
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='pedagogical_reading_runs'"
            ).fetchone()
            is None
        )
    assert database.migrate() == 14
    with database.connect() as connection:
        after = tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM sources),(SELECT count(*) FROM source_versions),"
                "(SELECT count(*) FROM document_structure_candidates),"
                "(SELECT count(*) FROM document_consolidated_topics)"
            ).fetchone()
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert after == before


def test_planner_preview_is_read_only_and_covers_all_51_topics(reading_library):
    database, version_id = reading_library
    service = PedagogicalReadingService(database)
    with database.connect() as connection:
        before = connection.total_changes
        memory_before = connection.execute(
            "SELECT (SELECT count(*) FROM pedagogical_concepts),"
            "(SELECT count(*) FROM pedagogical_evidence_locations)"
        ).fetchone()
    preview = service.preview_plan(version_id)
    assert preview["ready"] is True
    assert preview["planned_topics"] == 51
    assert [item["topic_number"] for item in preview["topics"]] == list(range(1, 52))
    assert all(item["block_count"] >= 1 for item in preview["topics"])
    with database.connect() as connection:
        assert connection.total_changes == before
        assert tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM pedagogical_concepts),"
                "(SELECT count(*) FROM pedagogical_evidence_locations)"
            ).fetchone()
        ) == tuple(memory_before)


@pytest.mark.anyio
async def test_atomic_execution_persists_provenance_without_confirming_memory(reading_library):
    database, version_id = reading_library
    router = FixtureRouter(unresolved=True)
    service = PedagogicalReadingService(database, router)  # type: ignore[arg-type]
    run = _create(service, version_id)
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE pedagogical_reading_topic_stages SET state='skipped' "
            "WHERE run_id=? AND topic_number>1",
            (run["id"],),
        )
    service._control(run["id"], "queued", None)
    finished = await service.execute(run["id"])
    assert finished["state"] == "completed_with_issues"
    assert finished["stop_reason"] == "awaiting_manual_next_pass"
    assert router.calls == [{"topic": 1, "interactive": False, "role": router.calls[0]["role"]}]
    with database.connect() as connection:
        candidate = connection.execute(
            "SELECT state,prompt_version,model,model_role,source_version_id,topic_number "
            "FROM pedagogical_reading_candidates WHERE run_id=?",
            (run["id"],),
        ).fetchone()
        assert tuple(candidate) == (
            "proposed",
            "pedagogical-reading.v1",
            "fixture-model",
            "deep",
            version_id,
            1,
        )
        assert (
            connection.execute("SELECT count(*) FROM pedagogical_reading_evidence").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT count(*) FROM pedagogical_reading_unresolved").fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM pedagogical_concepts WHERE editorial_status='confirmed'"
            ).fetchone()[0]
            == 0
        )
    coverage = service.coverage(run["id"])
    assert coverage["processed_blocks"] == len(run["stages"][0]["block_scope"])
    assert coverage["candidate_total"] == 1
    assert coverage["material_improvement"] is True
    next_pass = service.create_next_pass(run["id"])
    assert next_pass["pass_number"] == 2
    assert [stage["topic_number"] for stage in next_pass["stages"]] == [1]


@pytest.mark.anyio
async def test_outside_scope_output_is_repaired_once_with_explicit_invalid_ids(
    reading_library,
):
    database, version_id = reading_library
    initial = {
        "topic": 1,
        "evidence_scope": ["invented-block"],
        "candidates": [],
        "unresolved": ["Synthetic uncertainty"],
    }
    service = PedagogicalReadingService(database)
    run = _create(service, version_id)
    valid = {
        "topic": 1,
        "evidence_scope": [run["stages"][0]["block_scope"][0]],
        "candidates": [],
        "unresolved": ["Synthetic uncertainty"],
    }
    router = SequenceRouter([initial, valid])
    service.router = router  # type: ignore[assignment]
    _only_first_topic(database, run["id"])
    service._control(run["id"], "queued", None)

    finished = await service.execute(run["id"])

    stage = finished["stages"][0]
    assert stage["state"] == "completed_with_issues"
    assert len(router.calls) == 2
    repair_contract = router.calls[1]["messages"][-1]["content"]
    assert "invented-block" in repair_contract
    assert run["stages"][0]["block_scope"][0] in repair_contract


@pytest.mark.anyio
async def test_second_outside_scope_output_fails_topic_safely(reading_library):
    database, version_id = reading_library
    outside = {
        "topic": 1,
        "evidence_scope": ["invented-block"],
        "candidates": [],
        "unresolved": ["Synthetic uncertainty"],
    }
    router = SequenceRouter([outside, outside])
    service = PedagogicalReadingService(database, router)  # type: ignore[arg-type]
    run = _create(service, version_id)
    _only_first_topic(database, run["id"])
    service._control(run["id"], "queued", None)

    finished = await service.execute(run["id"])

    stage = finished["stages"][0]
    assert stage["state"] == "failed"
    assert "invented-block" in stage["error_detail"]
    assert len(router.calls) == 2
    with database.connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM pedagogical_reading_candidates WHERE run_id=?", (run["id"],)
        ).fetchone()[0] == 0


@pytest.mark.anyio
async def test_vacuous_output_is_repaired_once_without_candidate_quota(reading_library):
    database, version_id = reading_library
    vacuous = {"topic": 1, "evidence_scope": [], "candidates": []}
    service = PedagogicalReadingService(database)
    run = _create(service, version_id)
    valid = _candidate_output(database, run["stages"][0])
    router = SequenceRouter([vacuous, valid])
    service.router = router  # type: ignore[assignment]
    _only_first_topic(database, run["id"])
    service._control(run["id"], "queued", None)

    finished = await service.execute(run["id"])

    assert finished["stages"][0]["state"] == "completed"
    assert len(router.calls) == 2
    repair_contract = router.calls[1]["messages"][-1]["content"]
    assert "vacuous" in repair_contract
    assert "quota" in repair_contract


@pytest.mark.anyio
async def test_second_vacuous_output_is_not_cleanly_completed(reading_library):
    database, version_id = reading_library
    vacuous = {"topic": 1, "evidence_scope": [], "candidates": []}
    router = SequenceRouter([vacuous, vacuous])
    service = PedagogicalReadingService(database, router)  # type: ignore[arg-type]
    run = _create(service, version_id)
    _only_first_topic(database, run["id"])
    service._control(run["id"], "queued", None)

    finished = await service.execute(run["id"])

    assert finished["stages"][0]["state"] == "completed_with_issues"
    assert len(router.calls) == 2


@pytest.mark.anyio
async def test_unresolved_only_and_single_candidate_outputs_remain_valid(reading_library):
    database, version_id = reading_library
    unresolved = {
        "topic": 1,
        "evidence_scope": [],
        "candidates": [],
        "unresolved": ["Synthetic evidence remains ambiguous"],
    }
    unresolved_router = SequenceRouter([unresolved])
    unresolved_service = PedagogicalReadingService(
        database, unresolved_router  # type: ignore[arg-type]
    )
    unresolved_run = _create(unresolved_service, version_id)
    _only_first_topic(database, unresolved_run["id"])
    unresolved_service._control(unresolved_run["id"], "queued", None)
    unresolved_finished = await unresolved_service.execute(unresolved_run["id"])
    assert unresolved_finished["stages"][0]["state"] == "completed_with_issues"
    assert len(unresolved_router.calls) == 1

    low_volume_service = PedagogicalReadingService(database)
    low_volume_run = _create(low_volume_service, version_id)
    low_volume = _candidate_output(database, low_volume_run["stages"][0])
    low_volume_router = SequenceRouter([low_volume])
    low_volume_service.router = low_volume_router  # type: ignore[assignment]
    _only_first_topic(database, low_volume_run["id"])
    low_volume_service._control(low_volume_run["id"], "queued", None)
    low_volume_finished = await low_volume_service.execute(low_volume_run["id"])
    assert low_volume_finished["stages"][0]["state"] == "completed"
    assert len(low_volume_router.calls) == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM pedagogical_reading_candidates WHERE run_id=?",
            (low_volume_run["id"],),
        ).fetchone()[0] == 1


def test_out_of_scope_or_invented_evidence_is_rejected(reading_library):
    database, version_id = reading_library
    service = PedagogicalReadingService(database)
    run = _create(service, version_id)
    stage = run["stages"][0]
    output = PedagogicalReadingOutput.model_validate(
        {
            "topic": 1,
            "evidence_scope": stage["block_scope"],
            "candidates": [
                {
                    "local_id": "invented",
                    "candidate_type": "definition",
                    "content": {"statement": "Not observed"},
                    "evidence": [
                        {"block_id": stage["block_scope"][0], "quote": "invented quotation"}
                    ],
                    "confidence": 0.99,
                }
            ],
        }
    )
    assert service.persist_output(run["id"], stage["id"], output, model="fixture-model") == 1
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM pedagogical_reading_candidates WHERE run_id=?", (run["id"],)
            ).fetchone()[0]
            == 0
        )


def test_types_relations_conflicts_and_evidence_based_corroboration(reading_library):
    database, version_id = reading_library
    service = PedagogicalReadingService(database)
    first = _create(service, version_id)
    stage = first["stages"][0]
    assert len(stage["block_scope"]) >= 2
    with database.connect() as connection:
        blocks = [
            connection.execute(
                "SELECT raw_text FROM document_page_blocks WHERE id=?", (block_id,)
            ).fetchone()[0]
            for block_id in stage["block_scope"][:2]
        ]
    specs = [
        ("definition", {"statement": "A fixture definition"}),
        ("grammatical_rule", {"rule": "A fixture rule"}),
        ("example", {"example": "A fixture example"}),
        ("exception", {"exception": "A fixture exception"}),
        ("terminology", {"term": "Begriff"}),
    ]
    output = PedagogicalReadingOutput.model_validate(
        {
            "topic": 1,
            "evidence_scope": stage["block_scope"],
            "candidates": [
                {
                    "local_id": f"candidate-{index}",
                    "candidate_type": candidate_type,
                    "content": content,
                    "terminology_de": ["Begriff"],
                    "terminology_es": ["término"],
                    "evidence": [{"block_id": stage["block_scope"][0], "quote": blocks[0]}],
                    "confidence": 0.8,
                }
                for index, (candidate_type, content) in enumerate(specs)
            ],
            "relations": [
                {
                    "source_local_id": "candidate-1",
                    "target_local_id": "candidate-2",
                    "relation_type": "illustrated_by",
                }
            ],
            "conflicts": [
                {
                    "candidate_local_ids": ["candidate-1", "candidate-3"],
                    "description": "Fixture interpretations conflict",
                }
            ],
        }
    )
    assert service.persist_output(first["id"], stage["id"], output, model="fixture-model") == 0
    assert service.candidates_summary(first["id"])["counts"]
    assert len(service.conflicts(first["id"])) == 1
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE pedagogical_reading_topic_stages SET state=CASE WHEN topic_number=1 "
            "THEN 'completed_with_issues' ELSE 'skipped' END WHERE run_id=?",
            (first["id"],),
        )
        connection.execute(
            "UPDATE pedagogical_reading_runs SET state='completed_with_issues' WHERE id=?",
            (first["id"],),
        )
    second = service.create_next_pass(first["id"])
    second_stage = second["stages"][0]
    corroboration = PedagogicalReadingOutput.model_validate(
        {
            "topic": 1,
            "evidence_scope": second_stage["block_scope"],
            "candidates": [
                {
                    "local_id": "same-definition",
                    "candidate_type": "definition",
                    "content": {"statement": "A fixture definition"},
                    "evidence": [{"block_id": second_stage["block_scope"][1], "quote": blocks[1]}],
                    "confidence": 0.9,
                }
            ],
        }
    )
    assert (
        service.persist_output(
            second["id"], second_stage["id"], corroboration, model="fixture-model"
        )
        == 0
    )
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT state FROM pedagogical_reading_candidates WHERE run_id=?", (second["id"],)
            ).fetchone()[0]
            == "corroborated"
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM pedagogical_reading_candidates WHERE state='confirmed'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT state FROM pedagogical_reading_relations WHERE run_id=?", (first["id"],)
            ).fetchone()[0]
            == "proposed"
        )


def test_context_splits_at_blocks_and_max_pass_cancel_preserves_results(reading_library):
    database, version_id = reading_library
    service = PedagogicalReadingService(database)
    with database.connect() as connection:
        context = service._build_context(
            connection,
            version_id,
            1,
            {"input_character_budget": 10, "output_token_budget": 256},
        )
    assert len(context["units"]) == len(context["block_ids"])
    assert context["budget"]["split_reason"] == "structural_block_boundary"
    assert all(len(unit) == 1 for unit in context["units"])
    run = service.create_run(
        ReadingRunCreate(source_version_id=version_id, max_passes=1),
        resolved_model="fixture-model",
    )
    stage = run["stages"][0]
    with database.connect() as connection:
        quote = connection.execute(
            "SELECT raw_text FROM document_page_blocks WHERE id=?", (stage["block_scope"][0],)
        ).fetchone()[0]
    output = PedagogicalReadingOutput.model_validate(
        {
            "topic": 1,
            "evidence_scope": stage["block_scope"],
            "candidates": [
                {
                    "local_id": "max-pass",
                    "candidate_type": "definition",
                    "content": {"statement": "Still proposed"},
                    "evidence": [{"block_id": stage["block_scope"][0], "quote": quote}],
                    "confidence": 0.5,
                }
            ],
        }
    )
    service.persist_output(run["id"], stage["id"], output, model="fixture-model")
    with pytest.raises(Exception, match="maximum reading passes reached"):
        service.create_next_pass(run["id"])
    service._control(run["id"], "queued", None)
    assert service.cancel(run["id"])["state"] == "cancelled"
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM pedagogical_reading_candidates WHERE run_id=?", (run["id"],)
            ).fetchone()[0]
            == 1
        )


@pytest.mark.anyio
async def test_failed_topic_is_retryable_and_does_not_block_later_topics(reading_library):
    database, version_id = reading_library
    router = FixtureRouter(fail_topics={1})
    service = PedagogicalReadingService(database, router)  # type: ignore[arg-type]
    run = _create(service, version_id)
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE pedagogical_reading_topic_stages SET state='skipped' "
            "WHERE run_id=? AND topic_number>2",
            (run["id"],),
        )
    service._control(run["id"], "queued", None)
    finished = await service.execute(run["id"])
    states = {stage["topic_number"]: stage["state"] for stage in finished["stages"]}
    assert states[1] == "failed"
    assert states[2] == "completed"
    router.fail_topics.clear()
    retried = service.retry_failed(run["id"], [1])
    assert (
        next(stage for stage in retried["stages"] if stage["topic_number"] == 1)["state"]
        == "pending"
    )
    finished = await service.execute(run["id"])
    assert (
        next(stage for stage in finished["stages"] if stage["topic_number"] == 1)["state"]
        == "completed"
    )


@pytest.mark.anyio
async def test_opt_in_continuation_stops_when_next_pass_has_no_material_delta(reading_library):
    database, version_id = reading_library
    router = FixtureRouter(unresolved=True)
    service = PedagogicalReadingService(database, router)  # type: ignore[arg-type]
    run = _create_automatic(service, version_id)
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE pedagogical_reading_topic_stages SET state='skipped' "
            "WHERE run_id=? AND topic_number>1",
            (run["id"],),
        )
    service._control(run["id"], "queued", None)
    final_pass = await service.execute(run["id"])
    assert final_pass["pass_number"] == 2
    assert final_pass["stop_reason"] == "no_material_improvement"
    assert [call["topic"] for call in router.calls] == [1, 1]


def test_pause_resume_cancel_state_machine(reading_library):
    database, version_id = reading_library
    service = PedagogicalReadingService(database)
    run = _create(service, version_id)
    queued = service._control(run["id"], "queued", None)
    assert queued["state"] == "queued"
    assert service.pause(run["id"])["pause_reason"] == "manual"
    assert service.resume(run["id"])["state"] == "queued"
    assert service.cancel(run["id"])["state"] == "cancelled"


def test_process_restart_marks_active_reading_as_interrupted(reading_library):
    database, version_id = reading_library
    reading = PedagogicalReadingService(database)
    run = _create(reading, version_id)
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE pedagogical_reading_runs SET state='running' WHERE id=?", (run["id"],)
        )
        connection.execute(
            "UPDATE pedagogical_reading_topic_stages SET state='running' "
            "WHERE run_id=? AND topic_number=1",
            (run["id"],),
        )
    library = EducationalLibraryService.__new__(EducationalLibraryService)
    library.database = database
    library.runtime = database.path.parent
    library._recover_interrupted_jobs()
    recovered = reading.detail(run["id"])
    assert recovered["state"] == "interrupted"
    assert recovered["error_code"] == "process_restarted"
    assert recovered["stages"][0]["state"] == "failed"


@pytest.mark.anyio
async def test_reading_api_exposes_read_only_views_and_explicit_controls(
    reading_library, monkeypatch
):
    database, version_id = reading_library
    router = FixtureRouter()
    service = PedagogicalReadingService(database, router)  # type: ignore[arg-type]
    started_run_ids: list[str] = []

    async def execute(run_id: str) -> None:
        started_run_ids.append(run_id)

    monkeypatch.setattr(service, "execute", execute)
    app.dependency_overrides[get_pedagogical_reading] = lambda: service
    app.dependency_overrides[get_library_model_router] = lambda: router
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            readiness = await client.get(
                "/api/library/laboratory/reading/readiness",
                params={"source_version_id": version_id},
            )
            preview = await client.get(
                "/api/library/laboratory/reading/plan-preview",
                params={"source_version_id": version_id},
            )
            runs = await client.get("/api/library/laboratory/reading/runs")
            assert readiness.status_code == preview.status_code == runs.status_code == 200
            assert preview.json()["planned_topics"] == 51
            assert runs.json() == []
            created = await client.post(
                "/api/library/laboratory/reading/runs",
                json={"source_version_id": version_id, "auto_continue": False},
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            paused = await client.post(f"/api/library/laboratory/reading/runs/{run_id}/pause")
            assert paused.status_code == 422
            started = await client.post(
                f"/api/library/laboratory/reading/runs/{run_id}/start"
            )
            await asyncio.sleep(0)
            assert started.status_code == 202
            assert started.json()["state"] == "queued"
            assert started_run_ids == [run_id]
            paused = await client.post(f"/api/library/laboratory/reading/runs/{run_id}/pause")
            resumed = await client.post(f"/api/library/laboratory/reading/runs/{run_id}/resume")
            cancelled = await client.post(f"/api/library/laboratory/reading/runs/{run_id}/cancel")
            assert paused.json()["state"] == "paused"
            assert resumed.json()["state"] == "queued"
            assert cancelled.json()["state"] == "cancelled"
    finally:
        app.dependency_overrides.clear()
