"use client";

import type {
  KnowledgeUnit,
  LibraryChunk,
  LibraryJob,
  LibrarySearchResponse,
  LibrarySource,
  LibrarySourceVersion,
  LibrarySummary,
  TeacherConversationSummary,
  TeacherEvidenceConfidence,
  TeacherQuery,
} from "@deutschos/shared";
import type { FormEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  askLibrary,
  deleteTeacherConversation,
  excludeLibrarySource,
  getLibraryJobs,
  getLibraryKnowledge,
  getLibrarySourceChunks,
  getLibrarySourceVersions,
  getLibrarySources,
  getLibrarySummary,
  getTeacherConversations,
  getTeacherQuery,
  pauseLibraryJob,
  processLibraryPending,
  reprocessLibrarySource,
  retryLibraryJob,
  reviewKnowledge,
  scanLibrary,
  searchLibrary,
} from "../lib/api";

const EXAMPLE_QUESTIONS = [
  '¿Qué significa "die" en alemán?',
  "¿Cuál es la diferencia entre kein y nicht?",
  '¿Por qué se dice "den Hund"?',
  'Explícame la expresión "Ich hätte gerne".',
  "¿Qué fuentes de mi biblioteca explican el acusativo?",
];

const CONTINUATIONS = [
  {
    action: "expand",
    label: "Ampliar",
    question: "Amplía la explicación con un poco más de detalle.",
  },
  {
    action: "more_examples",
    label: "Más ejemplos",
    question: "Dame más ejemplos fundamentados en las mismas fuentes.",
  },
  {
    action: "rephrase",
    label: "Dicho más fácil",
    question: "Explícamelo de otra forma y con palabras más sencillas.",
  },
  {
    action: "use_in_sentence",
    label: "Usarlo en una frase",
    question: "Muéstrame cómo se usa en una frase breve.",
  },
] as const;

const PROGRESS_STEPS = [
  "Entendiendo la pregunta…",
  "Consultando tus materiales…",
  "Contrastando las fuentes…",
  "Preparando la explicación…",
];

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

function confidenceLabel(confidence: TeacherEvidenceConfidence): string {
  return {
    solid: "Evidencia sólida",
    moderate: "Evidencia moderada",
    limited: "Evidencia limitada",
    insufficient: "Evidencia insuficiente",
  }[confidence];
}

export function LibraryWorkspace() {
  const [summary, setSummary] = useState<LibrarySummary | null>(null);
  const [sources, setSources] = useState<LibrarySource[]>([]);
  const [jobs, setJobs] = useState<LibraryJob[]>([]);
  const [knowledge, setKnowledge] = useState<KnowledgeUnit[]>([]);
  const [history, setHistory] = useState<TeacherConversationSummary[]>([]);
  const [selectedSource, setSelectedSource] = useState<LibrarySource | null>(
    null,
  );
  const [chunks, setChunks] = useState<LibraryChunk[]>([]);
  const [versions, setVersions] = useState<LibrarySourceVersion[]>([]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<TeacherQuery | null>(null);
  const [rawQuery, setRawQuery] = useState("");
  const [mode, setMode] = useState<"lexical" | "semantic" | "hybrid">(
    "lexical",
  );
  const [results, setResults] = useState<LibrarySearchResponse | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [progressStep, setProgressStep] = useState(0);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    const [nextSummary, nextSources, nextJobs, nextKnowledge, nextHistory] =
      await Promise.all([
        getLibrarySummary(),
        getLibrarySources(),
        getLibraryJobs(),
        getLibraryKnowledge(),
        getTeacherConversations(),
      ]);
    setSummary(nextSummary);
    setSources(nextSources);
    setJobs(nextJobs);
    setKnowledge(nextKnowledge);
    setHistory(nextHistory);
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
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const activeJob = useMemo(
    () =>
      jobs.find((job) => ["queued", "running", "paused"].includes(job.state)),
    [jobs],
  );
  const answerSourceCount = answer
    ? new Set(answer.sources.map((source) => source.source_id)).size
    : 0;

  useEffect(() => {
    if (!activeJob || activeJob.state === "paused") return;
    const timer = window.setInterval(
      () => refresh().catch(() => undefined),
      2000,
    );
    return () => window.clearInterval(timer);
  }, [activeJob, refresh]);

  useEffect(() => {
    if (busy !== "ask") return;
    const timer = window.setInterval(
      () =>
        setProgressStep((current) =>
          Math.min(current + 1, PROGRESS_STEPS.length - 1),
        ),
      2200,
    );
    return () => window.clearInterval(timer);
  }, [busy]);

  async function ask(
    nextQuestion: string,
    continuation?: (typeof CONTINUATIONS)[number]["action"],
  ) {
    if (!nextQuestion.trim()) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy("ask");
    setProgressStep(0);
    setError("");
    try {
      const next = await askLibrary(
        {
          question: nextQuestion.trim(),
          conversation_id: continuation ? answer?.conversation_id : null,
          continuation_action: continuation ?? null,
        },
        controller.signal,
      );
      setAnswer(next);
      setQuestion("");
      setHistory(await getTeacherConversations());
    } catch (cause) {
      if (controller.signal.aborted) {
        setError(
          "Consulta cancelada en esta pantalla. El proceso local puede terminar en segundo plano.",
        );
      } else {
        setError(
          cause instanceof Error
            ? cause.message
            : "No se pudo preparar la explicación.",
        );
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setBusy("");
    }
  }

  function submitQuestion(event: FormEvent) {
    event.preventDefault();
    void ask(question);
  }

  async function openHistory(item: TeacherConversationSummary) {
    setBusy("history");
    setError("");
    try {
      setAnswer(await getTeacherQuery(item.latest_query_id));
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo abrir la consulta.",
      );
    } finally {
      setBusy("");
    }
  }

  async function removeHistory(conversationId: string) {
    setBusy("history");
    setError("");
    try {
      await deleteTeacherConversation(conversationId);
      if (answer?.conversation_id === conversationId) setAnswer(null);
      setHistory(await getTeacherConversations());
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo eliminar la consulta.",
      );
    } finally {
      setBusy("");
    }
  }

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

  async function openSourceDetail(sourceId: string) {
    const source = sources.find((item) => item.id === sourceId);
    if (!source) return;
    setAdvancedOpen(true);
    await selectSource(source);
    window.setTimeout(
      () =>
        document
          .getElementById("library-source-detail")
          ?.scrollIntoView({ behavior: "smooth" }),
      0,
    );
  }

  async function runSearch(event: FormEvent) {
    event.preventDefault();
    if (!rawQuery.trim()) return;
    setBusy("search");
    setError("");
    try {
      setResults(await searchLibrary(rawQuery.trim(), mode));
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Falló la búsqueda local.",
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
          <h1>Pregunta a tu biblioteca</h1>
        </div>
        <p>
          Explicaciones en español, ejemplos en alemán y fuentes verificables.
          Todo permanece en tu equipo.
        </p>
      </div>

      {error ? (
        <div className="error" role="alert">
          {error}
        </div>
      ) : null}

      <div
        className="library-metrics compact"
        aria-label="Resumen de biblioteca"
      >
        <article>
          <span>{summary?.total_sources ?? "—"}</span>
          <small>fuentes</small>
        </article>
        <article>
          <span>{summary?.chunks ?? "—"}</span>
          <small>fragmentos</small>
        </article>
        <article>
          <span>{summary?.knowledge_units ?? "—"}</span>
          <small>unidades revisables</small>
        </article>
        <article>
          <span>{summary?.pending ?? "—"}</span>
          <small>pendientes</small>
        </article>
      </div>

      <section className="teacher-ask" aria-labelledby="teacher-title">
        <p className="eyebrow">CONSULTA FUNDAMENTADA</p>
        <h2 id="teacher-title">¿Qué quieres entender o consultar?</h2>
        <p className="teacher-local-label">Basado en tu biblioteca local</p>
        <form onSubmit={submitQuestion}>
          <label htmlFor="teacher-question">
            Escribe una pregunta natural sobre alemán o sobre tus fuentes
          </label>
          <textarea
            id="teacher-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder='Por ejemplo: ¿por qué se dice "den Hund" y no "der Hund"?'
            maxLength={1000}
            rows={4}
          />
          <div className="question-examples" aria-label="Preguntas de ejemplo">
            {EXAMPLE_QUESTIONS.map((item) => (
              <button
                type="button"
                key={item}
                onClick={() => setQuestion(item)}
              >
                {item}
              </button>
            ))}
          </div>
          <div className="teacher-submit">
            <button
              className="primary"
              disabled={busy === "ask" || question.trim().length < 2}
            >
              Explicar <span>→</span>
            </button>
            {busy === "ask" ? (
              <button type="button" onClick={() => abortRef.current?.abort()}>
                Cancelar
              </button>
            ) : null}
          </div>
        </form>
        {busy === "ask" ? (
          <div className="teacher-progress" role="status" aria-live="polite">
            <span className="progress-pulse" />
            <strong>{PROGRESS_STEPS[progressStep]}</strong>
            <small>
              Qwen está leyendo únicamente la evidencia local recuperada.
            </small>
          </div>
        ) : null}
      </section>

      {answer ? (
        <article className="teacher-answer" aria-live="polite">
          <header>
            <div>
              <p className="eyebrow">RESPUESTA DEL PROFESOR LOCAL</p>
              <h2>{answer.question}</h2>
            </div>
            <span className={`confidence ${answer.confidence}`}>
              {confidenceLabel(answer.confidence)}
            </span>
          </header>
          <p className="direct-answer">{answer.answer.direct_answer}</p>
          {answer.answer.key_points.length ? (
            <ul className="answer-points">
              {answer.answer.key_points.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
          ) : null}
          {answer.answer.examples.length ? (
            <div className="teacher-examples">
              {answer.answer.examples.map((example, index) => (
                <section key={`${example.german}-${index}`}>
                  <strong lang="de">{example.german}</strong>
                  <span>{example.spanish}</span>
                  {example.note ? <small>{example.note}</small> : null}
                </section>
              ))}
            </div>
          ) : null}
          {answer.answer.important_nuance ? (
            <aside>
              <strong>Matiz importante</strong>
              <p>{answer.answer.important_nuance}</p>
            </aside>
          ) : null}
          {answer.answer.ambiguity_note ? (
            <aside className="warning">
              <strong>La pregunta admite más de una lectura</strong>
              <p>{answer.answer.ambiguity_note}</p>
            </aside>
          ) : null}
          {answer.answer.follow_up_question ? (
            <p className="follow-up-question">
              {answer.answer.follow_up_question}
            </p>
          ) : null}
          {answer.warnings.length ? (
            <ul className="answer-warnings">
              {answer.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          ) : null}
          <div
            className="continuation-actions"
            aria-label="Continuar la explicación"
          >
            {CONTINUATIONS.map((item) => (
              <button
                key={item.action}
                disabled={busy === "ask"}
                onClick={() => void ask(item.question, item.action)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <details className="answer-sources">
            <summary>
              Fuentes consultadas ({answerSourceCount}) · fragmentos (
              {answer.sources.length})
            </summary>
            {answer.sources.length ? (
              <ol>
                {answer.sources.map((source) => (
                  <li key={source.citation}>
                    <button
                      type="button"
                      className="source-detail-link"
                      onClick={() => void openSourceDetail(source.source_id)}
                    >
                      {source.citation} · {source.source_name}
                    </button>
                    <span>
                      {source.section ? `${source.section} · ` : ""}
                      {source.page_start
                        ? `p. ${source.page_start}`
                        : "ubicación interna"}
                    </span>
                    <blockquote>{source.snippet}</blockquote>
                  </li>
                ))}
              </ol>
            ) : (
              <p>
                La biblioteca no aportó evidencia suficiente para citar una
                fuente.
              </p>
            )}
          </details>
          <details className="answer-technical">
            <summary>Detalles técnicos de esta consulta</summary>
            <dl>
              <div>
                <dt>Recuperación</dt>
                <dd>
                  {answer.retrieval_mode}
                  {answer.semantic_search_available
                    ? " con índice semántico"
                    : " con fallback léxico"}
                </dd>
              </div>
              <div>
                <dt>Tiempo total</dt>
                <dd>{(answer.timings.total_ms / 1000).toFixed(1)} s</dd>
              </div>
              <div>
                <dt>Estado</dt>
                <dd>{answer.status}</dd>
              </div>
            </dl>
            {answer.sources.length ? (
              <ul className="technical-evidence">
                {answer.sources.map((source) => (
                  <li key={source.citation}>
                    {source.citation}: score {source.retrieval_score.toFixed(3)}{" "}
                    · calidad de extracción{" "}
                    {(source.extraction_quality * 100).toFixed(0)} % · rol{" "}
                    {source.content_role}
                  </li>
                ))}
              </ul>
            ) : null}
          </details>
        </article>
      ) : null}

      <section className="teacher-history library-panel">
        <div className="panel-title">
          <div>
            <p className="eyebrow">HISTORIAL LOCAL</p>
            <h2>Consultas recientes</h2>
          </div>
          <span>{history.length}</span>
        </div>
        {history.length ? (
          <div className="history-list">
            {history.map((item) => (
              <article key={item.conversation_id}>
                <button
                  className="history-open"
                  disabled={busy === "history"}
                  onClick={() => void openHistory(item)}
                >
                  <strong>{item.question}</strong>
                  <span>{item.answer_excerpt}</span>
                  <small>
                    {item.turn_count}{" "}
                    {item.turn_count === 1 ? "consulta" : "consultas"} ·{" "}
                    {item.source_count} fuentes ·{" "}
                    {confidenceLabel(item.confidence)}
                  </small>
                </button>
                <button
                  className="history-delete"
                  aria-label={`Eliminar ${item.question}`}
                  onClick={() => void removeHistory(item.conversation_id)}
                >
                  ×
                </button>
              </article>
            ))}
          </div>
        ) : (
          <p className="muted">Tus consultas fundamentadas aparecerán aquí.</p>
        )}
      </section>

      <details className="library-panel teacher-knowledge">
        <summary>Conocimiento aprendido y revisión</summary>
        <div className="knowledge-review-content">
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
                    {unit.kind} · confianza editorial orientativa ·{" "}
                    {unit.citations.length} fuentes
                  </small>
                  {["candidate", "needs_review", "conflict"].includes(
                    unit.status,
                  ) ? (
                    <div>
                      <button
                        disabled={busy === unit.id}
                        onClick={() => void review(unit, "approve")}
                      >
                        Aprobar
                      </button>
                      <button
                        disabled={busy === unit.id}
                        onClick={() => void review(unit, "reject")}
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
        </div>
      </details>

      <details
        className="advanced-library"
        open={advancedOpen}
        onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}
      >
        <summary>Herramientas avanzadas y estado de la biblioteca</summary>
        <div
          className="library-metrics compact"
          aria-label="Resumen de biblioteca"
        >
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
            <span>{summary?.knowledge_units ?? "—"}</span>
            <small>unidades</small>
          </article>
          <article>
            <span>{summary?.pending ?? "—"}</span>
            <small>pendientes</small>
          </article>
          <article>
            <span>{summary?.errors ?? "—"}</span>
            <small>errores</small>
          </article>
        </div>
        <p className="capability-line">
          FTS5 activo · semántica{" "}
          {summary?.capabilities.semantic_available
            ? "disponible"
            : "no configurada (fallback léxico)"}
        </p>
        <div className="library-actions">
          <button
            className="primary"
            disabled={Boolean(activeJob) || busy === "scan"}
            onClick={scan}
          >
            Escanear ahora
          </button>
          <button
            disabled={Boolean(activeJob)}
            onClick={() => processLibraryPending().then(refresh)}
          >
            Procesar pendientes
          </button>
          {activeJob ? (
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
          ) : null}
          {activeJob && activeJob.state !== "paused" ? (
            <button onClick={() => pauseLibraryJob(activeJob.id).then(refresh)}>
              Pausar
            </button>
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

        <section className="library-panel search-panel">
          <p className="eyebrow">EXPLORAR COINCIDENCIAS</p>
          <h2>Búsqueda técnica</h2>
          <form className="library-search" onSubmit={runSearch}>
            <input
              aria-label="Consulta técnica"
              value={rawQuery}
              onChange={(event) => setRawQuery(event.target.value)}
              placeholder="Buscar palabras exactas en los fragmentos"
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
          {results?.warning ? (
            <p className="warning">{results.warning}</p>
          ) : null}
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
              </article>
            ))}
          </div>
        </section>

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
                    onClick={() => void selectSource(source)}
                  >
                    <span>
                      <strong>{source.name}</strong>
                      <small>
                        {source.kind} · {source.format} ·{" "}
                        {humanBytes(source.size_bytes)}
                      </small>
                    </span>
                    <b className={`state ${source.processing_state}`}>
                      {source.processing_state}
                    </b>
                  </button>
                ))
              ) : (
                <p className="muted">
                  Escanea la carpeta para crear el catálogo.
                </p>
              )}
            </div>
          </section>
          <section
            className="library-panel source-detail"
            id="library-source-detail"
          >
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
                      reprocessLibrarySource(selectedSource.id).then(
                        selectSource,
                      )
                    }
                  >
                    Reprocesar
                  </button>
                  <button
                    onClick={() =>
                      excludeLibrarySource(selectedSource.id).then(
                        (updated) => {
                          setSelectedSource(updated);
                          return refresh();
                        },
                      )
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
                        v{version.version_number} · {version.processing_state} ·{" "}
                        {new Date(version.created_at).toLocaleString("es-ES")}
                      </li>
                    ))}
                  </ol>
                </details>
                <div className="chunk-list">
                  {chunks.map((chunk) => (
                    <article key={chunk.id}>
                      <header>
                        <strong>
                          {chunk.title ?? `Fragmento ${chunk.id}`}
                        </strong>
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
      </details>
    </section>
  );
}
