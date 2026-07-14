"use client";

import type {
  GroundedDraft,
  KnowledgeUnit,
  LibraryChunk,
  LibraryJob,
  LibrarySearchResponse,
  LibrarySource,
  LibrarySourceVersion,
  LibrarySummary,
} from "@deutschos/shared";
import type { FormEvent } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  generateGroundedDraft,
  excludeLibrarySource,
  getLibraryJobs,
  getLibraryKnowledge,
  getLibrarySourceChunks,
  getLibrarySourceVersions,
  getLibrarySources,
  getLibrarySummary,
  getModels,
  pauseLibraryJob,
  processLibraryPending,
  reprocessLibrarySource,
  retryLibraryJob,
  reviewKnowledge,
  scanLibrary,
  searchLibrary,
} from "../lib/api";

function humanBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  return `${(bytes / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function reference(
  chunk: Pick<
    LibraryChunk,
    "id" | "page_start" | "page_end" | "start_seconds" | "end_seconds"
  >,
): string {
  if (chunk.page_start)
    return `p. ${chunk.page_start}${chunk.page_end && chunk.page_end !== chunk.page_start ? `–${chunk.page_end}` : ""}`;
  if (chunk.start_seconds !== null)
    return `${Math.floor(chunk.start_seconds / 60)}:${String(Math.floor(chunk.start_seconds % 60)).padStart(2, "0")}`;
  return `fragmento ${chunk.id}`;
}

export function LibraryWorkspace() {
  const [summary, setSummary] = useState<LibrarySummary | null>(null);
  const [sources, setSources] = useState<LibrarySource[]>([]);
  const [jobs, setJobs] = useState<LibraryJob[]>([]);
  const [knowledge, setKnowledge] = useState<KnowledgeUnit[]>([]);
  const [selectedSource, setSelectedSource] = useState<LibrarySource | null>(
    null,
  );
  const [chunks, setChunks] = useState<LibraryChunk[]>([]);
  const [versions, setVersions] = useState<LibrarySourceVersion[]>([]);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"lexical" | "semantic" | "hybrid">(
    "lexical",
  );
  const [results, setResults] = useState<LibrarySearchResponse | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [objective, setObjective] = useState<
    "explanation" | "micro_lesson" | "exercises"
  >("micro_lesson");
  const [draft, setDraft] = useState<GroundedDraft | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const [nextSummary, nextSources, nextJobs, nextKnowledge] =
      await Promise.all([
        getLibrarySummary(),
        getLibrarySources(),
        getLibraryJobs(),
        getLibraryKnowledge(),
      ]);
    setSummary(nextSummary);
    setSources(nextSources);
    setJobs(nextJobs);
    setKnowledge(nextKnowledge);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      refresh().catch((cause: unknown) =>
        setError(
          cause instanceof Error
            ? cause.message
            : "No se pudo cargar la biblioteca.",
        ),
      );
      getModels()
        .then((value) => {
          const names = value.models.map((item) => item.name);
          setModels(names);
          setModel(names[0] ?? "");
        })
        .catch(() => undefined);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const activeJob = useMemo(
    () =>
      jobs.find((job) => ["queued", "running", "paused"].includes(job.state)),
    [jobs],
  );

  useEffect(() => {
    if (!activeJob || activeJob.state === "paused") return;
    const timer = window.setInterval(
      () => refresh().catch(() => undefined),
      2000,
    );
    return () => window.clearInterval(timer);
  }, [activeJob, refresh]);

  async function scan() {
    setBusy("scan");
    setError("");
    try {
      await scanLibrary();
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo iniciar el escaneo.",
      );
    } finally {
      setBusy("");
    }
  }

  async function selectSource(source: LibrarySource) {
    setSelectedSource(source);
    setBusy("source");
    setError("");
    try {
      const [nextChunks, nextVersions] = await Promise.all([
        getLibrarySourceChunks(source.id),
        getLibrarySourceVersions(source.id),
      ]);
      setChunks(nextChunks);
      setVersions(nextVersions);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudieron cargar los fragmentos.",
      );
    } finally {
      setBusy("");
    }
  }

  async function runSearch(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setBusy("search");
    setError("");
    try {
      setResults(await searchLibrary(query.trim(), mode));
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Falló la búsqueda local.",
      );
    } finally {
      setBusy("");
    }
  }

  async function generate(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setBusy("generate");
    setError("");
    try {
      setDraft(
        await generateGroundedDraft({
          query: query.trim(),
          level: "A1",
          objective,
          model: model || null,
        }),
      );
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Falló la generación fundamentada.",
      );
    } finally {
      setBusy("");
    }
  }

  async function review(unit: KnowledgeUnit, action: "approve" | "reject") {
    setBusy(unit.id);
    setError("");
    try {
      const updated = await reviewKnowledge(unit.id, action);
      setKnowledge((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      );
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo revisar la unidad.",
      );
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="library-shell">
      <div className="section-heading library-heading">
        <div>
          <p className="eyebrow">BIBLIOTECA EDUCATIVA LOCAL</p>
          <h1>Fuentes que se convierten en memoria.</h1>
        </div>
        <p>
          Los originales permanecen intactos. Catálogo, índices y borradores
          viven en un runtime reconstruible.
        </p>
      </div>
      {error ? (
        <div className="error" role="alert">
          {error}
        </div>
      ) : null}

      <div className="library-metrics" aria-label="Resumen de biblioteca">
        <article>
          <span>{summary?.total_sources ?? "—"}</span>
          <small>fuentes</small>
        </article>
        <article>
          <span>{summary ? humanBytes(summary.total_bytes) : "—"}</span>
          <small>catalogados</small>
        </article>
        <article>
          <span>{summary?.chunks ?? "—"}</span>
          <small>fragmentos</small>
        </article>
        <article>
          <span>{summary?.embeddings ?? "—"}</span>
          <small>embeddings</small>
        </article>
        <article>
          <span>{summary?.knowledge_units ?? "—"}</span>
          <small>unidades aprendidas</small>
        </article>
        <article>
          <span>{summary?.pending ?? "—"}</span>
          <small>pendientes</small>
        </article>
        <article>
          <span>{summary?.errors ?? "—"}</span>
          <small>con error</small>
        </article>
        <article>
          <span>{summary?.unsupported ?? "—"}</span>
          <small>no soportadas</small>
        </article>
        <article>
          <span>{summary?.needs_ocr ?? "—"}</span>
          <small>requieren OCR</small>
        </article>
        <article>
          <span>{summary?.needs_transcription ?? "—"}</span>
          <small>requieren transcripción</small>
        </article>
      </div>

      {summary?.latest_inventory ? (
        <div className="inventory-strip" aria-label="Último inventario">
          <span>{summary.latest_inventory.directories} carpetas</span>
          <span>{summary.latest_inventory.documents} documentos</span>
          <span>{summary.latest_inventory.audio} audios</span>
          <span>{summary.latest_inventory.video} vídeos</span>
          <span>{summary.latest_inventory.subtitles} subtítulos</span>
          <span>
            {summary.latest_inventory.confirmed_duplicate_files} duplicados
          </span>
        </div>
      ) : null}
      <p className="capability-line">
        FTS5 activo · semántica{" "}
        {summary?.capabilities.semantic_available
          ? "disponible"
          : "no configurada (fallback léxico)"}{" "}
        · transcripción{" "}
        {summary?.capabilities.transcription_backend ?? "pendiente"}
      </p>

      <div className="library-actions">
        <button
          className="primary"
          disabled={Boolean(activeJob) || busy === "scan"}
          onClick={scan}
        >
          Escanear ahora <span>→</span>
        </button>
        <button
          disabled={Boolean(activeJob)}
          onClick={() => processLibraryPending().then(refresh)}
        >
          Procesar pendientes
        </button>
        {activeJob ? (
          <>
            <div className="job-progress">
              <strong>{activeJob.state}</strong>
              <span>
                {activeJob.progress_current}/{activeJob.progress_total}
              </span>
              <progress
                max={Math.max(1, activeJob.progress_total)}
                value={activeJob.progress_current}
              />
            </div>
            {activeJob.state !== "paused" ? (
              <button
                onClick={() => pauseLibraryJob(activeJob.id).then(refresh)}
              >
                Pausar
              </button>
            ) : null}
          </>
        ) : null}
        {jobs.find((item) =>
          ["failed", "interrupted", "cancelled"].includes(item.state),
        ) ? (
          <button
            onClick={() =>
              retryLibraryJob(
                jobs.find((item) =>
                  ["failed", "interrupted", "cancelled"].includes(item.state),
                )!.id,
              ).then(refresh)
            }
          >
            Reintentar último error
          </button>
        ) : null}
      </div>

      <div className="library-columns">
        <section className="library-panel">
          <div className="panel-title">
            <div>
              <p className="eyebrow">CATÁLOGO</p>
              <h2>Fuentes</h2>
            </div>
            <span>{sources.length}</span>
          </div>
          <div className="source-list">
            {sources.length ? (
              sources.map((source) => (
                <button
                  className={
                    selectedSource?.id === source.id
                      ? "source-row selected"
                      : "source-row"
                  }
                  key={source.id}
                  onClick={() => selectSource(source)}
                >
                  <span>
                    <strong>{source.name}</strong>
                    <small>
                      {source.kind} · {source.format} ·{" "}
                      {humanBytes(source.size_bytes)}
                    </small>
                  </span>
                  <span>
                    <small>{source.language ?? "idioma pendiente"}</small>
                    <b className={`state ${source.processing_state}`}>
                      {source.processing_state}
                    </b>
                  </span>
                </button>
              ))
            ) : (
              <p className="muted">
                Escanea la carpeta para crear el catálogo.
              </p>
            )}
          </div>
        </section>

        <section className="library-panel source-detail">
          <p className="eyebrow">DETALLE Y PROVENANCE</p>
          {selectedSource ? (
            <>
              <h2>{selectedSource.name}</h2>
              <dl className="source-facts">
                <div>
                  <dt>Ruta</dt>
                  <dd>{selectedSource.current_path}</dd>
                </div>
                <div>
                  <dt>Versión</dt>
                  <dd>{selectedSource.current_version}</dd>
                </div>
                <div>
                  <dt>Derechos</dt>
                  <dd>{selectedSource.rights}</dd>
                </div>
                <div>
                  <dt>Nivel estimado</dt>
                  <dd>{selectedSource.cefr_level ?? "pendiente"}</dd>
                </div>
              </dl>
              <div className="library-actions">
                <button
                  onClick={() =>
                    reprocessLibrarySource(selectedSource.id).then(selectSource)
                  }
                >
                  Reprocesar
                </button>
                <button
                  onClick={() =>
                    excludeLibrarySource(selectedSource.id).then((updated) => {
                      setSelectedSource(updated);
                      return refresh();
                    })
                  }
                >
                  Excluir del índice
                </button>
              </div>
              <details>
                <summary>Historial de versiones ({versions.length})</summary>
                <ol>
                  {versions.map((version) => (
                    <li key={version.id}>
                      v{version.version_number} · {version.processing_state}
                      {version.error_code
                        ? ` · ${version.error_code}`
                        : ""} ·{" "}
                      {new Date(version.created_at).toLocaleString("es-ES")}
                    </li>
                  ))}
                </ol>
              </details>
              <div className="chunk-list">
                {chunks.map((chunk) => (
                  <article key={chunk.id}>
                    <header>
                      <strong>{chunk.title ?? `Fragmento ${chunk.id}`}</strong>
                      <small>
                        {reference(chunk)} · {chunk.content_role}
                      </small>
                    </header>
                    <p>
                      {chunk.text.slice(0, 600)}
                      {chunk.text.length > 600 ? "…" : ""}
                    </p>
                  </article>
                ))}
              </div>
            </>
          ) : (
            <div className="empty-inline">
              <h2>Selecciona una fuente</h2>
              <p>Verás metadatos y fragmentos, nunca el libro completo.</p>
            </div>
          )}
        </section>
      </div>

      <section className="library-panel search-panel">
        <p className="eyebrow">RECUPERACIÓN LOCAL</p>
        <h2>Buscar en la biblioteca</h2>
        <form className="library-search" onSubmit={runSearch}>
          <input
            aria-label="Consulta de biblioteca"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="p. ej. orden de la oración alemana"
            maxLength={1000}
          />
          <select
            aria-label="Modo de búsqueda"
            value={mode}
            onChange={(event) => setMode(event.target.value as typeof mode)}
          >
            <option value="lexical">Léxica FTS5</option>
            <option value="hybrid">Híbrida</option>
            <option value="semantic">Semántica</option>
          </select>
          <button className="primary" disabled={busy === "search"}>
            Buscar
          </button>
        </form>
        {results?.warning ? <p className="warning">{results.warning}</p> : null}
        <div className="search-results">
          {results?.results.map((result) => (
            <article key={result.id}>
              <header>
                <strong>{result.source_name}</strong>
                <small>
                  {reference(result)} · {results.effective_mode}
                </small>
              </header>
              <p>{result.snippet}</p>
              <small>
                score {result.combined_score.toFixed(4)} · versión{" "}
                {result.source_version}
              </small>
            </article>
          ))}
        </div>
      </section>

      <div className="library-columns">
        <section className="library-panel">
          <p className="eyebrow">CONOCIMIENTO APRENDIDO</p>
          <h2>Revisión humana</h2>
          <div className="knowledge-list">
            {knowledge.length ? (
              knowledge.map((unit) => (
                <article key={unit.id}>
                  <header>
                    <strong>{unit.title}</strong>
                    <b className={`state ${unit.status}`}>{unit.status}</b>
                  </header>
                  <p>{unit.content_es}</p>
                  <small>
                    {unit.kind} · confianza {(unit.confidence * 100).toFixed(0)}
                    % · {unit.citations.length} fuentes
                  </small>
                  {["candidate", "needs_review", "conflict"].includes(
                    unit.status,
                  ) ? (
                    <div>
                      <button
                        disabled={busy === unit.id}
                        onClick={() => review(unit, "approve")}
                      >
                        Aprobar
                      </button>
                      <button
                        disabled={busy === unit.id}
                        onClick={() => review(unit, "reject")}
                      >
                        Rechazar
                      </button>
                    </div>
                  ) : null}
                </article>
              ))
            ) : (
              <p className="muted">
                Las unidades generadas aparecerán aquí como candidatas
                revisables.
              </p>
            )}
          </div>
        </section>
        <section className="library-panel generation-panel">
          <p className="eyebrow">DEMOSTRACIÓN FUNDAMENTADA</p>
          <h2>Crear desde fuentes</h2>
          <form onSubmit={generate}>
            <label>
              Objetivo
              <select
                value={objective}
                onChange={(event) =>
                  setObjective(event.target.value as typeof objective)
                }
              >
                <option value="explanation">Explicación</option>
                <option value="micro_lesson">Microlección</option>
                <option value="exercises">Ejercicios originales</option>
              </select>
            </label>
            <label>
              Modelo
              <select
                value={model}
                onChange={(event) => setModel(event.target.value)}
              >
                <option value="">Modelo configurado</option>
                {models.map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </select>
            </label>
            <button
              className="primary"
              disabled={!query.trim() || busy === "generate"}
            >
              Generar con provenance
            </button>
          </form>
          {draft ? (
            <article className="grounded-draft">
              <span
                className={draft.evidence_sufficient ? "badge safe" : "badge"}
              >
                {draft.evidence_sufficient
                  ? "evidencia suficiente"
                  : "cobertura limitada"}
              </span>
              <h3>{draft.payload.title}</h3>
              <p>{draft.payload.explanation}</p>
              {draft.payload.examples.length ? (
                <ul>
                  {draft.payload.examples.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}
              <h4>Fuentes</h4>
              <ol>
                {draft.sources.map((source) => (
                  <li key={source.chunk_id}>
                    {source.source_name} ·{" "}
                    {source.page_start
                      ? `p. ${source.page_start}`
                      : source.start_seconds !== null
                        ? `${source.start_seconds}s`
                        : `fragmento ${source.chunk_id}`}{" "}
                    · v{source.source_version}
                  </li>
                ))}
              </ol>
              <button disabled>Borrador guardado localmente</button>
            </article>
          ) : null}
        </section>
      </div>
    </section>
  );
}
