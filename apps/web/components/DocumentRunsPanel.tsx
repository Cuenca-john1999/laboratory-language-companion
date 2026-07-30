"use client";

import type {
  CoverageSnapshot,
  DocumentRun,
  DocumentRunDetail,
  DocumentRunPageList,
  LaboratorySource,
} from "@deutschos/shared";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  cancelDocumentRun,
  createDocumentRun,
  createDocumentRunPass,
  executeDocumentPreflight,
  getDocumentRun,
  getDocumentRunPages,
  getDocumentRuns,
  pauseDocumentRun,
  reconcileDocumentCoverage,
  resumeDocumentRun,
  retryDocumentRunStage,
} from "../lib/api";

const RUN_LABELS: Record<DocumentRun["state"], string> = {
  planned: "Planificada",
  queued: "En cola",
  running: "En curso",
  paused: "Pausada",
  completed: "Completada",
  completed_with_issues: "Completada con incidencias",
  failed: "Fallida",
  cancelled: "Cancelada",
  stale: "Obsoleta",
  superseded: "Sustituida",
};

const DIMENSIONS = [
  ["file", "Archivo"],
  ["pages", "Páginas"],
  ["text", "Texto"],
  ["quality", "Calidad"],
  ["structure", "Estructura"],
  ["review", "Revisión"],
  ["chunks", "Chunks"],
  ["embeddings", "Embeddings"],
  ["canonical_mapping", "Mapeo canónico"],
] as const;

const PAGE_FILTERS = [
  ["all", "Todas"],
  ["pending", "Pendientes"],
  ["completed", "Completadas"],
  ["issues", "Con incidencias"],
  ["failed", "Fallidas"],
  ["needs_review", "Necesitan revisión"],
  ["without_text", "Sin texto"],
] as const;

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("es-ES", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function duration(run: DocumentRun): string {
  if (!run.started_at) return "Sin iniciar";
  const end = run.completed_at ? new Date(run.completed_at) : new Date();
  const seconds = Math.max(
    0,
    Math.floor((end.getTime() - new Date(run.started_at).getTime()) / 1000),
  );
  if (seconds < 60) return `${seconds} s`;
  return `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

function latestStage(run: DocumentRunDetail, name: string) {
  return run.stages
    .filter((stage) => stage.name === name)
    .sort((left, right) => right.attempt - left.attempt)[0];
}

function coverageValue(snapshot: CoverageSnapshot | undefined) {
  if (!snapshot || snapshot.execution_status === "not_executed") {
    return { label: "No ejecutado", percent: null };
  }
  if (snapshot.denominator === null) {
    return { label: "Sin denominador", percent: null };
  }
  const completed = snapshot.completed ?? 0;
  return {
    label: `${completed} / ${snapshot.denominator}`,
    percent:
      snapshot.denominator === 0
        ? 0
        : Math.round((completed / snapshot.denominator) * 100),
  };
}

export function DocumentRunsPanel({
  sources,
}: {
  sources: LaboratorySource[];
}) {
  const [runs, setRuns] = useState<DocumentRun[]>([]);
  const [selected, setSelected] = useState<DocumentRunDetail | null>(null);
  const [pages, setPages] = useState<DocumentRunPageList | null>(null);
  const [pageFilter, setPageFilter] = useState("all");
  const [pageNumber, setPageNumber] = useState(1);
  const [targetVersion, setTargetVersion] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const eligibleSources = useMemo(
    () => sources.filter((source) => source.format.toLowerCase() === ".pdf"),
    [sources],
  );

  const refreshRuns = useCallback(async () => {
    setRuns(await getDocumentRuns());
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      refreshRuns()
        .catch((cause: unknown) =>
          setError(
            cause instanceof Error
              ? cause.message
              : "No se pudieron cargar las ejecuciones.",
          ),
        )
        .finally(() => setLoading(false));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshRuns]);

  const loadPages = useCallback(
    async (runId: string, nextPage: number, nextFilter: string) => {
      setPages(await getDocumentRunPages(runId, nextPage, nextFilter));
    },
    [],
  );

  async function openRun(runId: string) {
    setError("");
    setPageFilter("all");
    setPageNumber(1);
    try {
      const detail = await getDocumentRun(runId);
      setSelected(detail);
      await loadPages(runId, 1, "all");
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo cargar el detalle de la ejecución.",
      );
    }
  }

  async function mutate(action: () => Promise<DocumentRunDetail>) {
    setBusy(true);
    setError("");
    try {
      const detail = await action();
      setSelected(detail);
      await Promise.all([
        refreshRuns(),
        loadPages(detail.id, pageNumber, pageFilter),
      ]);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "La operación documental no pudo completarse.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function createRun() {
    if (!targetVersion) return;
    setBusy(true);
    setError("");
    try {
      const detail = await createDocumentRun(Number(targetVersion));
      setSelected(detail);
      setPages({
        items: [],
        page: 1,
        page_size: 25,
        total: 0,
        pages: 0,
      });
      await refreshRuns();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo planificar la ejecución.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function changePages(nextPage: number, nextFilter: string) {
    if (!selected) return;
    setPageNumber(nextPage);
    setPageFilter(nextFilter);
    try {
      await loadPages(selected.id, nextPage, nextFilter);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudieron cargar las páginas.",
      );
    }
  }

  const latestCoverage = useMemo(() => {
    const values = new Map<string, CoverageSnapshot>();
    for (const snapshot of selected?.coverage ?? []) {
      values.set(snapshot.dimension, snapshot);
    }
    return values;
  }, [selected]);

  const preflight = selected ? latestStage(selected, "preflight") : undefined;
  const reconciliation = selected
    ? latestStage(selected, "coverage_reconciliation")
    : undefined;
  const failedStage = selected
    ? selected.stages
        .filter((stage) => stage.state === "failed")
        .sort((left, right) => right.attempt - left.attempt)[0]
    : undefined;
  const terminal = selected
    ? [
        "completed",
        "completed_with_issues",
        "cancelled",
        "stale",
        "superseded",
      ].includes(selected.state)
    : false;

  return (
    <>
      <section className="document-runs" aria-labelledby="document-runs-title">
        <header>
          <div>
            <p className="eyebrow">Procesamiento versionado</p>
            <h2 id="document-runs-title">Ejecuciones</h2>
            <p>
              Cada pasada pertenece a una versión y un hash exactos. No modifica
              por sí sola la versión activa.
            </p>
          </div>
          <div className="run-create">
            <label htmlFor="run-version">Versión objetivo exacta</label>
            <select
              id="run-version"
              onChange={(event) => setTargetVersion(event.target.value)}
              value={targetVersion}
            >
              <option value="">Selecciona una versión</option>
              {eligibleSources
                .filter((source) => source.latest_version_id !== null)
                .map((source) => (
                  <option
                    key={`${source.id}-${source.latest_version_id}`}
                    value={source.latest_version_id ?? ""}
                  >
                    {source.title} · v{source.latest_version_number}
                  </option>
                ))}
            </select>
            <button
              className="primary-button"
              disabled={busy || !targetVersion}
              onClick={createRun}
              type="button"
            >
              Crear ejecución
            </button>
          </div>
        </header>

        <div className="run-principles">
          <span>Una versión nueva requiere ejecuciones nuevas.</span>
          <span>Repetir una pasada conserva la evidencia anterior.</span>
          <span>La cobertura no equivale a comprensión.</span>
          <span>Las páginas problemáticas permanecen localizables.</span>
        </div>

        {error ? (
          <p className="laboratory-feedback error" role="alert">
            {error}
          </p>
        ) : null}

        {loading ? (
          <p className="laboratory-empty">Cargando ejecuciones…</p>
        ) : runs.length === 0 ? (
          <p className="laboratory-empty">
            Todavía no existen ejecuciones documentales.
          </p>
        ) : (
          <div className="run-list">
            {runs.map((run) => (
              <button
                className="run-row"
                key={run.id}
                onClick={() => openRun(run.id)}
                type="button"
              >
                <span>
                  <strong>{run.source_title}</strong>
                  <small>
                    v{run.source_version_number} ·{" "}
                    {run.target_hash.slice(0, 12)}
                  </small>
                </span>
                <span>
                  <strong>{run.pipeline_version}</strong>
                  <small>{run.current_stage ?? "Sin etapa activa"}</small>
                </span>
                <span className={`run-state ${run.state}`}>
                  {RUN_LABELS[run.state]}
                </span>
                <span>
                  <strong>{run.issue_count} incidencias</strong>
                  <small>
                    {formatDate(run.created_at)} · {duration(run)}
                  </small>
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      {selected ? (
        <div
          aria-label={`Ejecución de ${selected.source_title}`}
          aria-modal="true"
          className="laboratory-dialog-backdrop"
          role="dialog"
        >
          <section className="laboratory-dialog run-dialog">
            <header>
              <div>
                <p className="eyebrow">Ejecución versionada</p>
                <h2>{selected.source_title}</h2>
                <p>
                  Versión {selected.source_version_number} ·{" "}
                  {selected.target_hash.slice(0, 16)} ·{" "}
                  {selected.pipeline_version}
                </p>
              </div>
              <button
                aria-label="Cerrar detalle de ejecución"
                onClick={() => setSelected(null)}
                type="button"
              >
                Cerrar
              </button>
            </header>

            <div className="run-detail-meta">
              <span className={`run-state ${selected.state}`}>
                {RUN_LABELS[selected.state]}
              </span>
              <span>
                Estrategia: <strong>{selected.selection_strategy}</strong>
              </span>
              <span>
                Anterior:{" "}
                <strong>
                  {selected.parent_run_id?.slice(0, 8) ?? "ninguna"}
                </strong>
              </span>
              <span>
                Duración: <strong>{duration(selected)}</strong>
              </span>
            </div>

            {selected.error_detail ? (
              <p className="laboratory-feedback error" role="alert">
                {selected.error_detail} ({selected.error_code})
              </p>
            ) : null}

            <div className="run-actions" aria-label="Acciones de ejecución">
              {preflight?.state === "pending" &&
              !["paused", "cancelled", "stale"].includes(selected.state) ? (
                <button
                  disabled={busy}
                  onClick={() =>
                    mutate(() => executeDocumentPreflight(selected.id))
                  }
                  type="button"
                >
                  Ejecutar preflight
                </button>
              ) : null}
              {preflight?.state === "completed" &&
              reconciliation?.state === "pending" &&
              selected.state !== "paused" ? (
                <button
                  disabled={busy}
                  onClick={() =>
                    mutate(() => reconcileDocumentCoverage(selected.id))
                  }
                  type="button"
                >
                  Reconciliar cobertura
                </button>
              ) : null}
              {["queued", "running"].includes(selected.state) ? (
                <button
                  disabled={busy}
                  onClick={() => mutate(() => pauseDocumentRun(selected.id))}
                  type="button"
                >
                  Pausar
                </button>
              ) : null}
              {selected.state === "paused" && selected.resumable ? (
                <button
                  disabled={busy}
                  onClick={() => mutate(() => resumeDocumentRun(selected.id))}
                  type="button"
                >
                  Reanudar
                </button>
              ) : null}
              {!terminal ? (
                <button
                  disabled={busy}
                  onClick={() => mutate(() => cancelDocumentRun(selected.id))}
                  type="button"
                >
                  Cancelar
                </button>
              ) : null}
              {selected.state === "failed" && failedStage ? (
                <button
                  disabled={busy}
                  onClick={() =>
                    mutate(() =>
                      retryDocumentRunStage(selected.id, failedStage.name),
                    )
                  }
                  type="button"
                >
                  Reintentar {failedStage.name}
                </button>
              ) : null}
              {selected.state === "completed_with_issues" ? (
                <button
                  disabled={busy}
                  onClick={() =>
                    mutate(() => createDocumentRunPass(selected.id))
                  }
                  type="button"
                >
                  Crear pasada desde pendientes
                </button>
              ) : null}
            </div>

            <section className="run-detail-section">
              <header>
                <h3>Etapas</h3>
                <span>{selected.stages.length} registros de intento</span>
              </header>
              <div className="stage-list">
                {selected.stages.map((stage) => (
                  <article key={stage.id}>
                    <strong>{stage.name}</strong>
                    <span>{stage.version}</span>
                    <span>Intento {stage.attempt}</span>
                    <span className={`run-state ${stage.state}`}>
                      {stage.state === "not_scheduled"
                        ? "No ejecutado"
                        : stage.state}
                    </span>
                  </article>
                ))}
              </div>
            </section>

            <section className="run-detail-section">
              <header>
                <h3>Cobertura verificable</h3>
                <span>Dimensiones independientes</span>
              </header>
              <div className="coverage-grid">
                {DIMENSIONS.map(([dimension, label]) => {
                  const value = coverageValue(latestCoverage.get(dimension));
                  return (
                    <article key={dimension}>
                      <span>{label}</span>
                      <strong>{value.label}</strong>
                      {value.percent !== null ? (
                        <div
                          aria-label={`${label}: ${value.label}`}
                          aria-valuemax={100}
                          aria-valuemin={0}
                          aria-valuenow={value.percent}
                          className="coverage-track"
                          role="progressbar"
                        >
                          <span style={{ width: `${value.percent}%` }} />
                        </div>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            </section>

            <section className="run-detail-section">
              <header>
                <h3>Páginas</h3>
                <span>{pages?.total ?? 0} resultados</span>
              </header>
              <div
                className="laboratory-filters"
                aria-label="Filtros de páginas"
              >
                {PAGE_FILTERS.map(([value, label]) => (
                  <button
                    aria-pressed={pageFilter === value}
                    key={value}
                    onClick={() => changePages(1, value)}
                    type="button"
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="page-table-wrap">
                <table className="page-table">
                  <thead>
                    <tr>
                      <th>Página PDF</th>
                      <th>Impresa</th>
                      <th>Texto</th>
                      <th>Calidad</th>
                      <th>Estructura</th>
                      <th>Revisión</th>
                      <th>Chunks</th>
                      <th>Embeddings</th>
                      <th>Incidencias</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pages?.items.map((item) => (
                      <tr key={item.id}>
                        <td>{item.pdf_page_number}</td>
                        <td>{item.printed_page_number ?? "—"}</td>
                        <td>
                          {item.has_text === null
                            ? "Sin datos"
                            : item.has_text
                              ? "Sí"
                              : "No"}
                        </td>
                        <td>{item.text_quality ?? "No ejecutado"}</td>
                        <td>
                          {item.stage_states.structural_extraction ??
                            "No ejecutado"}
                        </td>
                        <td>{item.stage_states.review ?? "No ejecutado"}</td>
                        <td>{item.stage_states.chunking ?? "No ejecutado"}</td>
                        <td>
                          {item.stage_states.embeddings ?? "No ejecutado"}
                        </td>
                        <td>{item.issue_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="page-pagination">
                <button
                  disabled={!pages || pageNumber <= 1}
                  onClick={() => changePages(pageNumber - 1, pageFilter)}
                  type="button"
                >
                  Anterior
                </button>
                <span>
                  Página {pageNumber} de {pages?.pages || 1}
                </span>
                <button
                  disabled={!pages || pageNumber >= pages.pages}
                  onClick={() => changePages(pageNumber + 1, pageFilter)}
                  type="button"
                >
                  Siguiente
                </button>
              </div>
            </section>

            <details className="run-technical">
              <summary>Configuración y eventos técnicos</summary>
              <pre>{JSON.stringify(selected.configuration, null, 2)}</pre>
              <ol>
                {selected.events.map((event) => (
                  <li key={event.id}>
                    <time dateTime={event.created_at}>
                      {formatDate(event.created_at)}
                    </time>{" "}
                    · {event.event_type}
                    {event.to_state ? ` → ${event.to_state}` : ""}
                  </li>
                ))}
              </ol>
            </details>
          </section>
        </div>
      ) : null}
    </>
  );
}
