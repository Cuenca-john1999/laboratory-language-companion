from __future__ import annotations

from pathlib import Path

import pytest

from llc_api.core.config import Settings
from llc_api.educational_library.canonical_route import CanonicalRouteService
from llc_api.educational_library.dependencies import get_canonical_route
from llc_api.educational_library.editorial import LibraryEditorialService
from llc_api.educational_library.schemas import (
    CanonicalRouteRevertRequest,
    CanonicalTopicReviewRequest,
    CoreSourceAssignmentRequest,
    LibraryContractError,
    ReferenceIndexEntry,
    ReferenceIndexPage,
)
from llc_api.educational_library.service import EducationalLibraryService, utc_text
from llc_api.main import app

_STARTS = [
    27,
    33,
    42,
    46,
    55,
    61,
    65,
    70,
    80,
    90,
    93,
    99,
    103,
    108,
    115,
    122,
    129,
    135,
    142,
    147,
    155,
    161,
    168,
    179,
    189,
    197,
    203,
    214,
    224,
    228,
    231,
    235,
    247,
    252,
    257,
    291,
    300,
    305,
    312,
    319,
    323,
    330,
    334,
    339,
    343,
    350,
    354,
    357,
    362,
    366,
    369,
]
_TITLES = {
    1: ("La conjugación de los verbos", "Die Konjugation der Verben"),
    9: ("El pretérito perfecto", "Das Perfekt"),
    20: ("El género", "Das Genus"),
    22: ("La declinación del sustantivo", "Die Deklination des Substantivs"),
    28: ("Los pronombres personales", "Personalpronomen"),
    40: ("La negación", "Die Negation"),
    51: ("Oraciones subordinadas finales", "Finalsätze"),
}


def _entry(
    *,
    level: str,
    number: int | None,
    title_es: str,
    page: int | None,
    title_de: str | None = None,
    local_number: str | None = None,
    status: str = "verified",
) -> ReferenceIndexEntry:
    return ReferenceIndexEntry(
        hierarchy_level=level,
        theme_number=number,
        local_number=local_number,
        parent_path=[] if number is None else [f"Tema {number}"],
        title_es=title_es,
        title_de=title_de,
        printed_page_label=str(page) if page else None,
        raw_visible_text=title_es,
        confidence=1,
        visual_region="unknown",
        parse_status="verified",
        editorial_status=status,
    )


def canonical_pages() -> list[ReferenceIndexPage]:
    by_page: dict[int, list[ReferenceIndexEntry]] = {page: [] for page in range(1, 18)}
    by_page[1].append(_entry(level="front_matter", number=None, title_es="Prólogo", page=5))
    for number, printed in enumerate(_STARTS, start=1):
        reference_page = min(17, (number - 1) // 3 + 1)
        title_es, title_de = _TITLES.get(
            number, (f"Tema sintético {number}", f"Synthetisches Thema {number}")
        )
        by_page[reference_page].extend(
            [
                _entry(
                    level="top_level_theme",
                    number=number,
                    title_es=title_es,
                    title_de=title_de,
                    page=printed,
                ),
                _entry(
                    level="section",
                    number=number,
                    local_number="1",
                    title_es=f"Apartado del tema {number}",
                    page=printed,
                ),
            ]
        )
    by_page[7].extend(
        [
            _entry(
                level="subsection",
                number=20,
                local_number="2",
                title_es="Son neutros (das)",
                page=149,
            ),
            _entry(
                level="subsection",
                number=20,
                local_number="3",
                title_es="Son femeninos (die)",
                page=151,
            ),
            _entry(
                level="section",
                number=20,
                local_number="3",
                title_es="Otras observaciones generales",
                page=153,
                status="user_confirmed",
            ),
            _entry(
                level="subsection",
                number=22,
                local_number="2",
                title_es="El acusativo (complemento directo)",
                title_de="Akkusativ",
                page=162,
            ),
        ]
    )
    by_page[17].append(
        _entry(
            level="back_matter",
            number=None,
            title_es="Verbos fuertes e irregulares más importantes",
            page=373,
        )
    )
    return [
        ReferenceIndexPage(
            reference_pdf_page=page,
            physical_index_page=page,
            entries=by_page[page],
        )
        for page in range(1, 18)
    ]


@pytest.fixture
def route_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    materials = tmp_path / "materials"
    materials.mkdir()
    (materials / "Herder Gramatica Hispanohablantes.md").write_text("# Manual\n", encoding="utf-8")
    (materials / "Herder Ejercicios.md").write_text("# Ejercicios\n", encoding="utf-8")
    library = EducationalLibraryService(
        Settings(
            database_url="sqlite://",
            educational_materials_dir=materials,
            educational_library_runtime_dir=tmp_path / "runtime",
            educational_library_scan_on_startup=False,
            educational_library_embedding_model="",
        )
    )
    library.scan()
    editorial = LibraryEditorialService(library.database)
    candidates = {item.suggested_role.value: item.source for item in editorial.candidates()}
    theory = candidates["core_theory"]
    workbook = candidates["core_workbook"]
    editorial.assign_core(
        theory.id,
        CoreSourceAssignmentRequest(
            operation_id="canonical-test-theory",
            pedagogical_role="core_theory",
            related_source_id=workbook.id,
        ),
    )
    editorial.assign_core(
        workbook.id,
        CoreSourceAssignmentRequest(
            operation_id="canonical-test-workbook",
            pedagogical_role="core_workbook",
            related_source_id=theory.id,
        ),
    )
    with library.database.transaction(immediate=True) as connection:
        version_id = connection.execute(
            "SELECT current_version_id FROM sources WHERE id=?", (theory.id,)
        ).fetchone()[0]
        for number in range(1, 52):
            title = _TITLES.get(number, (f"Tema sintético {number}", None))[0]
            connection.execute(
                "INSERT INTO editorial_sections(source_version_id,stable_key,title,page_start,"
                "page_end,content_role,derivation_method,provenance_confidence,editorial_status,"
                "created_at,updated_at) VALUES (?,?,?,?,?,'theory','test',1,"
                "'system_suggested',?,?)",
                (
                    version_id,
                    f"legacy-{number:02d}",
                    f"Tema {number}. {title}",
                    20 + number,
                    20 + number,
                    utc_text(),
                    utc_text(),
                ),
            )
        for index in (1, 2):
            connection.execute(
                "INSERT INTO editorial_sections(source_version_id,stable_key,title,page_start,"
                "page_end,content_role,derivation_method,provenance_confidence,editorial_status,"
                "created_at,updated_at) VALUES (?,?,?,?,?,'theory','test',1,"
                "'system_suggested',?,?)",
                (
                    version_id,
                    f"false-index-{index}",
                    f"Tema {index}. índice",
                    index,
                    index,
                    utc_text(),
                    utc_text(),
                ),
            )
    reference = tmp_path / "Herder_Index_verified.pdf"
    reference.write_bytes(b"%PDF synthetic reference")
    monkeypatch.setattr(
        CanonicalRouteService,
        "inspect_reference",
        staticmethod(lambda path: ("a" * 64, path.stat().st_size, 17)),
    )
    return library, CanonicalRouteService(library.database), reference


def test_validation_and_canonical_controls():
    result = CanonicalRouteService.validate_pages(canonical_pages())
    assert result.valid
    assert result.theme_count == 51
    assert not result.missing_theme_numbers
    assert not result.duplicate_theme_numbers
    assert all(result.canonical_checks.values())


def test_import_is_atomic_idempotent_searchable_and_non_destructive(route_fixture):
    library, route, reference = route_fixture
    imported = route.import_reference(
        reference,
        canonical_pages(),
        model="fixture",
        parser_version="fixture.v1",
    )
    assert imported.active
    assert route.status().topic_count == 51
    assert len(route.list_topics()) == 51
    assert route.topic(22).printed_start == 161
    assert route.list_topics("Akkusativ")[0].theme_number == 22
    assert route.list_topics("Perfekt")[0].theme_number == 9
    assert route.list_topics("página 319")[0].theme_number == 40
    mappings = route.mappings()
    assert len(mappings) == 53
    assert sum(item.mapping_status == "exact" for item in mappings) == 51
    assert sum(item.mapping_status == "rejected" for item in mappings) == 2
    repeated = route.import_reference(
        reference,
        canonical_pages(),
        model="fixture",
        parser_version="fixture.v1",
    )
    assert repeated.id == imported.id
    assert repeated.idempotent_replay
    with library.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM editorial_sections").fetchone()[0] == 53
        assert connection.execute("SELECT count(*) FROM canonical_topics").fetchone()[0] == 51


def test_review_audit_and_revert(route_fixture):
    _library, route, reference = route_fixture
    route.import_reference(reference, canonical_pages(), model="fixture")
    corrected = route.review_topic(
        3,
        CanonicalTopicReviewRequest(
            operation_id="canonical-correct-theme-3",
            action="correct",
            title_es="Título revisado",
            comment="Prueba editorial",
        ),
    )
    assert corrected.title_es == "Título revisado"
    replayed = route.review_topic(
        3,
        CanonicalTopicReviewRequest(
            operation_id="canonical-correct-theme-3",
            action="correct",
            title_es="Título revisado",
            comment="Prueba editorial",
        ),
    )
    assert replayed.title_es == "Título revisado"
    with pytest.raises(LibraryContractError):
        route.review_topic(
            3,
            CanonicalTopicReviewRequest(
                operation_id="canonical-correct-theme-3",
                action="correct",
                title_es="Otro título",
                comment="Prueba editorial",
            ),
        )
    audit = route.audits()[0]
    reverted = route.revert(
        CanonicalRouteRevertRequest(
            operation_id="canonical-revert-theme-3",
            audit_id=audit.id,
        )
    )
    assert reverted.action == "revert"
    assert (
        route.revert(
            CanonicalRouteRevertRequest(
                operation_id="canonical-revert-theme-3",
                audit_id=audit.id,
            )
        ).id
        == reverted.id
    )
    assert route.topic(3).title_es == "Tema sintético 3"


def test_legacy_key_and_monotonic_order_are_probable_not_silently_exact(route_fixture):
    library, route, reference = route_fixture
    with library.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE editorial_sections SET stable_key='topic-022',title='fragmento OCR parcial' "
            "WHERE stable_key='legacy-22'"
        )
    route.import_reference(reference, canonical_pages(), model="fixture")
    mapping = next(item for item in route.mappings() if item.legacy_stable_key == "topic-022")
    assert mapping.theme_number == 22
    assert mapping.mapping_status == "probable"
    assert "legacy_stable_key:22" in mapping.evidence
    assert "monotonic_neighbors:true" in mapping.evidence
    assert route.resolve_legacy_exact(mapping.legacy_section_id) is None


def test_legacy_reconciliation_is_audited_and_idempotent(route_fixture):
    library, route, reference = route_fixture
    route.import_reference(reference, canonical_pages(), model="fixture")
    first = route.reconcile_legacy("canonical-reconcile-test")
    replay = route.reconcile_legacy("canonical-reconcile-test")
    assert len(first) == len(replay) == 53
    assert route.audits()[0].action == "reconcile"
    with library.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM editorial_sections").fetchone()[0] == 53


def test_route_search_uses_verified_bilingual_concept_aliases(route_fixture):
    library, route, reference = route_fixture
    route.import_reference(reference, canonical_pages(), model="fixture")
    with library.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE canonical_outline_nodes SET title_de=NULL WHERE title_de='Akkusativ'"
        )
        connection.execute(
            "INSERT INTO pedagogical_concepts(id,canonical_name,normalized_name,language,"
            "display_name_es,display_name_de,category,editorial_status,created_at,updated_at) "
            "VALUES ('concept-akkusativ','Akkusativ','akkusativ','de','Acusativo','Akkusativ',"
            "'grammar','user_confirmed',?,?)",
            (utc_text(), utc_text()),
        )
    assert 22 in [topic.theme_number for topic in route.list_topics("Akkusativ")]


def test_invalid_route_never_replaces_active(route_fixture):
    _library, route, reference = route_fixture
    active = route.import_reference(reference, canonical_pages(), model="fixture")
    broken = canonical_pages()
    broken[0] = broken[0].model_copy(
        update={
            "entries": [
                entry
                for entry in broken[0].entries
                if not (entry.hierarchy_level == "top_level_theme" and entry.theme_number == 1)
            ]
        }
    )
    reference.write_bytes(b"%PDF changed reference")
    original_inspector = CanonicalRouteService.inspect_reference
    CanonicalRouteService.inspect_reference = staticmethod(  # type: ignore[method-assign]
        lambda path: ("b" * 64, path.stat().st_size, 17)
    )
    try:
        candidate = route.import_reference(reference, broken, model="fixture")
    finally:
        CanonicalRouteService.inspect_reference = original_inspector  # type: ignore[method-assign]
    assert not candidate.active
    assert route.status().active_import.id == active.id


@pytest.mark.anyio
async def test_canonical_route_http_contract(client, route_fixture):
    _library, route, reference = route_fixture
    route.import_reference(reference, canonical_pages(), model="fixture")
    app.dependency_overrides[get_canonical_route] = lambda: route
    try:
        status = await client.get("/api/library/canonical-route/status")
        assert status.status_code == 200
        assert status.json()["topic_count"] == 51
        topics = await client.get("/api/library/canonical-route/topics?query=Finals%C3%A4tze")
        assert topics.status_code == 200
        assert [item["theme_number"] for item in topics.json()] == [51]
        payload = topics.json()[0]
        assert "reference_sha256" not in str(payload)
        assert "raw_visible_text" not in str(payload)
        reviewed = await client.post(
            "/api/library/canonical-route/topics/51/review",
            json={
                "operation_id": "http-confirm-theme-51",
                "action": "confirm",
            },
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["editorial_status"] == "user_confirmed"
    finally:
        app.dependency_overrides.pop(get_canonical_route, None)
