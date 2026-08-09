"use client";

import type {
  DocumentPageBlock,
  DocumentRun,
  DocumentRunDetail,
  DocumentRunPageList,
  LaboratorySource,
  ProposedHierarchyNode,
  StructureCandidate,
  StructureCandidateList,
} from "@deutschos/shared";
import Image from "next/image";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  analyzeStructuredLayout,
  compareStructuredVersion,
  createStructuredExtractionRun,
  executeStructuredPreflight,
  extractStructureCandidates,
  extractStructuredText,
  getDocumentRun,
  getDocumentRunPages,
  getDocumentRuns,
  getProposedHierarchy,
  getStructureCandidate,
  getStructureCandidates,
  getStructuredPageBlocks,
  materializeStructuredPages,
  reconcileStructuredCoverage,
  repeatStructuredPages,
  structuredThumbnailUrl,
} from "../lib/api";

const STAGES = [
  ["pdf_preflight", "Preflight", executeStructuredPreflight],
  ["page_materialization", "Páginas", materializeStructuredPages],
  ["embedded_text_extraction", "Texto", extractStructuredText],
  ["layout_analysis", "Layout", analyzeStructuredLayout],
  ["structure_candidate_extraction", "Estructura", extractStructureCandidates],
  ["coverage_reconciliation", "Cobertura", reconcileStructuredCoverage],
  ["version_comparison", "Comparación", compareStructuredVersion],
] as const;

const CANDIDATE_TYPES = [
  "topic_heading",
  "section_heading",
  "subsection_heading",
  "level_marker",
  "example",
  "table",
  "diagram",
  "exercise_heading",
  "exercise_instruction",
  "exercise_item",
  "solution_heading",
  "solution_item",
  "paragraph",
] as const;

function TreeNode({ node }: { node: ProposedHierarchyNode }) {
  const label =
    node.candidate.raw_text?.slice(0, 100) ?? node.candidate.candidate_type;
  if (!node.children.length) {
    return (
      <li>
        <span>{label}</span>
        <small>p. PDF {node.candidate.pdf_page_number}</small>
      </li>
    );
  }
  return (
    <li>
      <details>
        <summary>
          {label} <small>· {node.children.length} bloques</small>
        </summary>
        <ul>
          {node.children.map((child) => (
            <TreeNode key={child.candidate.id} node={child} />
          ))}
        </ul>
      </details>
    </li>
  );
}

export function StructuredExtractionPanel({
  sources,
}: {
  sources: LaboratorySource[];
}) {
  const [runs, setRuns] = useState<DocumentRun[]>([]);
  const [selected, setSelected] = useState<DocumentRunDetail | null>(null);
  const [targetVersion, setTargetVersion] = useState("");
  const [pages, setPages] = useState<DocumentRunPageList | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [expandedPage, setExpandedPage] = useState<number | null>(null);
  const [blocks, setBlocks] = useState<DocumentPageBlock[]>([]);
  const [candidateList, setCandidateList] =
    useState<StructureCandidateList | null>(null);
  const [candidateDetail, setCandidateDetail] =
    useState<StructureCandidate | null>(null);
  const [hierarchy, setHierarchy] = useState<ProposedHierarchyNode[]>([]);
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [topicFilter, setTopicFilter] = useState("");
  const [levelFilter, setLevelFilter] = useState("");
  const [candidatePage, setCandidatePage] = useState("");
  const [minConfidence, setMinConfidence] = useState("");
  const [withIssue, setWithIssue] = useState(false);
  const [withoutParent, setWithoutParent] = useState(false);
  const [ambiguous, setAmbiguous] = useState(false);
  const [repeatInput, setRepeatInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const eligible = useMemo(
    () =>
      sources.filter(
        (source) =>
          source.format.toLowerCase() === ".pdf" &&
          source.latest_version_id !== null,
      ),
    [sources],
  );

  const refreshRuns = useCallback(async () => {
    const values = await getDocumentRuns();
    setRuns(values.filter((run) => run.run_type === "structured_extraction"));
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshRuns().catch(() =>
        setError("No se pudieron cargar las extracciones estructurales."),
      );
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshRuns]);

  const candidateParameters = useCallback(
    (run: DocumentRunDetail, requestedPage = 1) => {
      const params = new URLSearchParams({
        source_version_id: String(run.source_version_id),
        page: String(requestedPage),
        page_size: "25",
      });
      if (typeFilter) params.set("candidate_type", typeFilter);
      if (statusFilter) params.set("status", statusFilter);
      if (topicFilter) params.set("topic", topicFilter);
      if (levelFilter) params.set("level", levelFilter);
      if (candidatePage) params.set("pdf_page_number", candidatePage);
      if (minConfidence) params.set("min_confidence", minConfidence);
      if (withIssue) params.set("with_issue", "true");
      if (withoutParent) params.set("without_parent", "true");
      if (ambiguous) params.set("ambiguous", "true");
      return params;
    },
    [
      ambiguous,
      candidatePage,
      levelFilter,
      minConfidence,
      statusFilter,
      topicFilter,
      typeFilter,
      withIssue,
      withoutParent,
    ],
  );

  const loadDetail = useCallback(
    async (runId: string) => {
      const detail = await getDocumentRun(runId);
      const [nextPages, nextCandidates, nextHierarchy] = await Promise.all([
        getDocumentRunPages(runId, 1, "all"),
        getStructureCandidates(candidateParameters(detail)),
        getProposedHierarchy(detail.source_version_id),
      ]);
      setSelected(detail);
      setPages(nextPages);
      setCandidateList(nextCandidates);
      setHierarchy(nextHierarchy);
      setPageNumber(1);
      setExpandedPage(null);
      setBlocks([]);
    },
    [candidateParameters],
  );

  async function createRun() {
    if (!targetVersion) return;
    setBusy(true);
    setError("");
    try {
      const detail = await createStructuredExtractionRun(Number(targetVersion));
      await refreshRuns();
      await loadDetail(detail.id);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo crear la extracción.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function execute(
    action: (runId: string) => Promise<DocumentRunDetail>,
  ) {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const detail = await action(selected.id);
      await refreshRuns();
      await loadDetail(detail.id);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "La etapa no pudo completarse.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function changePage(nextPage: number) {
    if (!selected) return;
    setPages(await getDocumentRunPages(selected.id, nextPage, "all"));
    setPageNumber(nextPage);
    setExpandedPage(null);
  }

  async function inspectPage(pdfPage: number) {
    if (!selected) return;
    if (expandedPage === pdfPage) {
      setExpandedPage(null);
      return;
    }
    setBlocks(await getStructuredPageBlocks(selected.id, pdfPage));
    setExpandedPage(pdfPage);
  }

  async function applyCandidateFilters(requestedPage = 1) {
    if (!selected) return;
    setCandidateList(
      await getStructureCandidates(
        candidateParameters(selected, requestedPage),
      ),
    );
  }

  async function repeatPages() {
    if (!selected) return;
    const values = repeatInput
      .split(",")
      .map((value) => Number(value.trim()))
      .filter((value) => Number.isInteger(value) && value > 0);
    if (!values.length) {
      setError("Indica una o varias páginas PDF separadas por comas.");
      return;
    }
    setBusy(true);
    try {
      const child = await repeatStructuredPages(selected.id, values);
      await refreshRuns();
      await loadDetail(child.id);
      setRepeatInput("");
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo repetir la página.",
      );
    } finally {
      setBusy(false);
    }
  }

  const latestCoverage = useMemo(() => {
    const values = new Map<
      string,
      { completed: number | null; denominator: number | null }
    >();
    for (const snapshot of selected?.coverage ?? []) {
      if (!values.has(snapshot.dimension))
        values.set(snapshot.dimension, snapshot);
    }
    return values;
  }, [selected]);

  return (
    <section
      className="structured-extraction"
      aria-labelledby="structured-extraction-title"
    >
      <header>
        <div>
          <p className="eyebrow">PDF observado → candidatos propuestos</p>
          <h2 id="structured-extraction-title">Extracción</h2>
          <p>
            Procesa únicamente texto ya incrustado y conserva estructura,
            evidencia e incidencias. No confirma conocimiento ni ejecuta OCR o
            IA.
          </p>
        </div>
        <div className="run-create">
          <label htmlFor="extraction-version">Versión candidata exacta</label>
          <select
            id="extraction-version"
            onChange={(event) => setTargetVersion(event.target.value)}
            value={targetVersion}
          >
            <option value="">Selecciona una versión</option>
            {eligible.map((source) => (
              <option key={source.id} value={source.latest_version_id ?? ""}>
                {source.title} · v{source.latest_version_number}
              </option>
            ))}
          </select>
          <button
            disabled={busy || !targetVersion}
            onClick={createRun}
            type="button"
          >
            Crear extracción
          </button>
        </div>
      </header>

      {error ? (
        <p className="laboratory-feedback error" role="alert">
          {error}
        </p>
      ) : null}

      <div
        className="extraction-run-list"
        aria-label="Extracciones disponibles"
      >
        {runs.map((run) => (
          <button
            key={run.id}
            onClick={() => void loadDetail(run.id)}
            type="button"
          >
            <strong>{run.source_title}</strong>
            <span>
              v{run.source_version_number} · {run.target_hash.slice(0, 12)}
            </span>
            <small>{run.state.replaceAll("_", " ")}</small>
          </button>
        ))}
      </div>

      {selected ? (
        <div className="extraction-workspace">
          <section className="extraction-summary">
            <header>
              <div>
                <p className="eyebrow">Versión exacta</p>
                <h3>{selected.source_title}</h3>
              </div>
              <code>{selected.target_hash}</code>
            </header>
            <div className="extraction-stage-grid">
              {STAGES.map(([name, label, action]) => {
                const stage = selected.stages.find(
                  (item) => item.name === name,
                );
                const executable =
                  stage?.state === "pending" || stage?.state === "queued";
                return (
                  <button
                    disabled={busy || !executable}
                    key={name}
                    onClick={() => void execute(action)}
                    type="button"
                  >
                    <strong>{label}</strong>
                    <span>
                      {stage?.state.replaceAll("_", " ") ?? "no disponible"}
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="extraction-coverage">
              {[
                "pages",
                "text",
                "layout",
                "structure",
                "topics_detected",
                "candidates",
                "review_pending",
              ].map((dimension) => {
                const value = latestCoverage.get(dimension);
                return (
                  <span key={dimension}>
                    <strong>
                      {value
                        ? `${value.completed ?? 0} / ${value.denominator ?? 0}`
                        : "—"}
                    </strong>
                    {dimension.replaceAll("_", " ")}
                  </span>
                );
              })}
            </div>
          </section>

          <section className="extraction-pages">
            <header>
              <div>
                <p className="eyebrow">Paginado para corpus extensos</p>
                <h3>Explorador de páginas</h3>
              </div>
              <div className="repeat-pages">
                <label htmlFor="repeat-pages">Repetir páginas PDF</label>
                <input
                  id="repeat-pages"
                  onChange={(event) => setRepeatInput(event.target.value)}
                  placeholder="12, 18, 19"
                  value={repeatInput}
                />
                <button
                  disabled={busy}
                  onClick={() => void repeatPages()}
                  type="button"
                >
                  Crear pasada
                </button>
              </div>
            </header>
            <div className="extraction-page-list">
              {pages?.items.map((page) => (
                <article key={page.id}>
                  <button
                    onClick={() => void inspectPage(page.pdf_page_number)}
                    type="button"
                  >
                    <strong>PDF {page.pdf_page_number}</strong>
                    <span>
                      Impresa: {page.printed_page_number ?? "desconocida"}
                    </span>
                    <span>
                      {page.has_text
                        ? `${page.character_count ?? 0} caracteres`
                        : "Sin texto observado"}
                    </span>
                    <small>
                      {page.layout_state ?? "layout pendiente"} ·{" "}
                      {page.issue_count} incidencias
                    </small>
                  </button>
                  {expandedPage === page.pdf_page_number ? (
                    <div className="page-observation">
                      <Image
                        alt={`Miniatura técnica de la página PDF ${page.pdf_page_number}`}
                        height={360}
                        src={structuredThumbnailUrl(
                          selected.source_version_id,
                          page.pdf_page_number,
                        )}
                        unoptimized
                        width={260}
                      />
                      <ol>
                        {blocks.slice(0, 40).map((block) => (
                          <li key={block.id}>
                            <strong>{block.block_type}</strong>
                            <span>
                              {block.raw_text?.slice(0, 180) ??
                                "Región sin texto"}
                            </span>
                          </li>
                        ))}
                      </ol>
                    </div>
                  ) : null}
                </article>
              ))}
            </div>
            <nav
              aria-label="Paginación de páginas"
              className="extraction-pagination"
            >
              <button
                disabled={pageNumber <= 1}
                onClick={() => void changePage(pageNumber - 1)}
                type="button"
              >
                Anterior
              </button>
              <span>
                Página {pageNumber} de {pages?.pages ?? 0}
              </span>
              <button
                disabled={!pages || pageNumber >= pages.pages}
                onClick={() => void changePage(pageNumber + 1)}
                type="button"
              >
                Siguiente
              </button>
            </nav>
          </section>

          <section className="candidate-explorer">
            <header>
              <div>
                <p className="eyebrow">Evidencia sin confirmación editorial</p>
                <h3>Explorador de candidatos</h3>
              </div>
              <strong>{candidateList?.total ?? 0} candidatos</strong>
            </header>
            <div className="candidate-filters">
              <select
                aria-label="Tipo"
                onChange={(event) => setTypeFilter(event.target.value)}
                value={typeFilter}
              >
                <option value="">Todos los tipos</option>
                {CANDIDATE_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
              <select
                aria-label="Estado"
                onChange={(event) => setStatusFilter(event.target.value)}
                value={statusFilter}
              >
                <option value="">Todos los estados</option>
                <option value="proposed">Propuesto</option>
                <option value="auto_supported">Apoyado por reglas</option>
                <option value="needs_review">Necesita revisión</option>
              </select>
              <input
                aria-label="Tema"
                onChange={(event) => setTopicFilter(event.target.value)}
                placeholder="Tema"
                value={topicFilter}
              />
              <input
                aria-label="Nivel"
                onChange={(event) => setLevelFilter(event.target.value)}
                placeholder="G / M / O"
                value={levelFilter}
              />
              <input
                aria-label="Página PDF"
                min="1"
                onChange={(event) => setCandidatePage(event.target.value)}
                placeholder="Página"
                type="number"
                value={candidatePage}
              />
              <input
                aria-label="Confianza mínima"
                max="1"
                min="0"
                onChange={(event) => setMinConfidence(event.target.value)}
                placeholder="Confianza"
                step="0.1"
                type="number"
                value={minConfidence}
              />
              <label>
                <input
                  checked={withIssue}
                  onChange={(event) => setWithIssue(event.target.checked)}
                  type="checkbox"
                />{" "}
                Con incidencia
              </label>
              <label>
                <input
                  checked={withoutParent}
                  onChange={(event) => setWithoutParent(event.target.checked)}
                  type="checkbox"
                />{" "}
                Sin padre
              </label>
              <label>
                <input
                  checked={ambiguous}
                  onChange={(event) => setAmbiguous(event.target.checked)}
                  type="checkbox"
                />{" "}
                Ambiguos
              </label>
              <button
                onClick={() => void applyCandidateFilters()}
                type="button"
              >
                Filtrar
              </button>
            </div>
            <div className="candidate-list">
              {candidateList?.items.map((candidate) => (
                <button
                  key={candidate.id}
                  onClick={async () =>
                    setCandidateDetail(
                      await getStructureCandidate(candidate.id),
                    )
                  }
                  type="button"
                >
                  <strong>{candidate.candidate_type}</strong>
                  <span>
                    {candidate.raw_text?.slice(0, 180) ?? "Región sin texto"}
                  </span>
                  <small>
                    PDF {candidate.pdf_page_number} ·{" "}
                    {Math.round(candidate.confidence * 100)} % ·{" "}
                    {candidate.status}
                  </small>
                </button>
              ))}
            </div>
            {candidateDetail ? (
              <aside className="candidate-detail">
                <header>
                  <strong>{candidateDetail.candidate_type}</strong>
                  <button
                    onClick={() => setCandidateDetail(null)}
                    type="button"
                  >
                    Cerrar
                  </button>
                </header>
                <dl>
                  <div>
                    <dt>Texto observado</dt>
                    <dd>{candidateDetail.raw_text ?? "Sin texto"}</dd>
                  </div>
                  <div>
                    <dt>Normalización de layout</dt>
                    <dd>
                      {candidateDetail.normalized_layout_text ??
                        "No disponible"}
                    </dd>
                  </div>
                  <div>
                    <dt>Jerarquía</dt>
                    <dd>
                      {candidateDetail.parent_candidate_id ??
                        "Sin padre propuesto"}
                    </dd>
                  </div>
                  <div>
                    <dt>Evidencia</dt>
                    <dd>
                      <code>{JSON.stringify(candidateDetail.evidence)}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>Extractor</dt>
                    <dd>
                      {candidateDetail.extractor} ·{" "}
                      {candidateDetail.extractor_version}
                    </dd>
                  </div>
                  <div>
                    <dt>Incidencias</dt>
                    <dd>
                      {candidateDetail.issues.length
                        ? JSON.stringify(candidateDetail.issues)
                        : "Ninguna"}
                    </dd>
                  </div>
                </dl>
              </aside>
            ) : null}
          </section>

          <section className="proposed-hierarchy">
            <header>
              <p className="eyebrow">Append-only y plegable</p>
              <h3>Jerarquía propuesta</h3>
            </header>
            {hierarchy.length ? (
              <ul>
                {hierarchy.map((node) => (
                  <TreeNode key={node.candidate.id} node={node} />
                ))}
              </ul>
            ) : (
              <p className="laboratory-empty">
                La jerarquía todavía no se ha extraído.
              </p>
            )}
          </section>
        </div>
      ) : null}
    </section>
  );
}
