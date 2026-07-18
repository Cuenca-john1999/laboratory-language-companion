"use client";

import type {
  CoreSourcePair,
  EditorialSection,
  KnowledgeUnit,
  LibraryChunk,
  LibraryJob,
  LibrarySearchResponse,
  LibrarySource,
  LibrarySourceVersion,
  LibrarySummary,
  LibraryModelRouting,
  PageQuality,
  PedagogicalConcept,
  PedagogicalConceptSummary,
  PedagogicalMemorySummary,
  MemoryAudit,
  MemoryFeedbackVerdict,
  MemoryReviewQueueItem,
  TeacherConversationSummary,
  TeacherEvidenceConfidence,
  TeacherQuery,
} from "@deutschos/shared";
import type { FormEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  askLibrary,
  analyzeLibraryPages,
  assignLibraryCore,
  buildLibrarySections,
  deleteTeacherConversation,
  excludeLibrarySource,
  getLibraryJobs,
  getLibraryCore,
  getLibraryKnowledge,
  getLibrarySourceChunks,
  getLibrarySourceVersions,
  getLibrarySources,
  getLibrarySummary,
  getLibrarySections,
  getLibraryPageQuality,
  getLibraryModelRoles,
  getMemoryAudit,
  getMemoryReviewQueue,
  getPedagogicalConcept,
  getPedagogicalConcepts,
  getPedagogicalMemorySummary,
  getTeacherConversations,
  getTeacherQuery,
  pauseLibraryJob,
  indexLibrarySemantic,
  processLibraryPending,
  reprocessLibrarySource,
  retryLibraryJob,
  reviewKnowledge,
  reviewPedagogicalMemory,
  revertPedagogicalMemoryReview,
  scanLibrary,
  searchLibrary,
  streamLibraryAnswer,
  sendTeacherLocationFeedback,
  sendTeacherResponseFeedback,
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
  const [core, setCore] = useState<CoreSourcePair | null>(null);
  const [modelRouting, setModelRouting] = useState<LibraryModelRouting | null>(
    null,
  );
  const [selectedSource, setSelectedSource] = useState<LibrarySource | null>(
    null,
  );
  const [chunks, setChunks] = useState<LibraryChunk[]>([]);
  const [versions, setVersions] = useState<LibrarySourceVersion[]>([]);
  const [sections, setSections] = useState<EditorialSection[]>([]);
  const [pageQuality, setPageQuality] = useState<PageQuality[]>([]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<TeacherQuery | null>(null);
  const [memorySummary, setMemorySummary] =
    useState<PedagogicalMemorySummary | null>(null);
  const [concepts, setConcepts] = useState<PedagogicalConceptSummary[]>([]);
  const [reviewQueue, setReviewQueue] = useState<MemoryReviewQueueItem[]>([]);
  const [selectedMemoryConcept, setSelectedMemoryConcept] =
    useState<PedagogicalConcept | null>(null);
  const [memoryAudit, setMemoryAudit] = useState<MemoryAudit[]>([]);
  const [memorySearch, setMemorySearch] = useState("");
  const [editingLocationId, setEditingLocationId] = useState<string | null>(
    null,
  );
  const [locationDraft, setLocationDraft] = useState({
    scan_layout: "unknown",
    region: "unknown",
    printed_left_label: "",
    printed_right_label: "",
    printed_full_label: "",
  });
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
    const [
      nextSummary,
      nextSources,
      nextJobs,
      nextKnowledge,
      nextHistory,
      nextCore,
      nextRouting,
      nextMemory,
      nextConcepts,
      nextQueue,
    ] = await Promise.all([
      getLibrarySummary(),
      getLibrarySources(),
      getLibraryJobs(),
      getLibraryKnowledge(),
      getTeacherConversations(),
      getLibraryCore(),
      getLibraryModelRoles(),
      getPedagogicalMemorySummary(),
      getPedagogicalConcepts(),
      getMemoryReviewQueue(),
    ]);
    setSummary(nextSummary);
    setSources(nextSources);
    setJobs(nextJobs);
    setKnowledge(nextKnowledge);
    setHistory(nextHistory);
    setCore(nextCore);
    setModelRouting(nextRouting);
    setMemorySummary(nextMemory);
    setConcepts(nextConcepts);
    setReviewQueue(nextQueue);
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
      const payload = {
        question: nextQuestion.trim(),
        conversation_id: continuation ? answer?.conversation_id : null,
        continuation_action: continuation ?? null,
      };
      let next: TeacherQuery | null = null;
      for await (const event of streamLibraryAnswer(
        payload,
        controller.signal,
      )) {
        if (event.event === "planning") setProgressStep(1);
        if (event.event === "retrieving") setProgressStep(2);
        if (event.event === "generating" || event.event === "provisional") {
          setProgressStep(3);
        }
        if (event.event === "error") {
          throw new Error(event.message);
        }
        if (event.query) next = event.query;
      }
      if (!next) {
        // Compatibility fallback for an older local API during rolling updates.
        next = await askLibrary(payload, controller.signal);
      }
      setAnswer(next);
      setQuestion("");
      setHistory(await getTeacherConversations());
    } catch (cause) {
      if (controller.signal.aborted) {
        setError(
          "Consulta cancelada y registrada sin una respuesta no verificada.",
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

  async function assignCoreCandidate(
    source: LibrarySource,
    role: "core_theory" | "core_workbook" | "core_answer_key",
  ) {
    setBusy("core");
    setError("");
    const related = core?.candidates.find(
      (candidate) =>
        candidate.source.id !== source.id &&
        ((role === "core_theory" &&
          candidate.suggested_role === "core_workbook") ||
          (role === "core_workbook" &&
            candidate.suggested_role === "core_theory")),
    )?.source.id;
    try {
      await assignLibraryCore(source.id, {
        operation_id: crypto.randomUUID(),
        pedagogical_role: role,
        display_alias: source.name
          .replace(/^\d+_CORE_/, "")
          .replaceAll("_", " ")
          .replace(/\.[^.]+$/, ""),
        related_source_id: related ?? null,
        editorial_notes: "Selección confirmada desde la biblioteca local.",
      });
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo confirmar la fuente nuclear.",
      );
    } finally {
      setBusy("");
    }
  }

  async function prepareSections() {
    if (!selectedSource) return;
    setBusy("sections");
    try {
      setSections(await buildLibrarySections(selectedSource.id));
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo preparar el índice.",
      );
    } finally {
      setBusy("");
    }
  }

  async function analyzePages() {
    if (!selectedSource) return;
    setBusy("pages");
    try {
      setPageQuality(await analyzeLibraryPages(selectedSource.id));
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo analizar la extracción.",
      );
    } finally {
      setBusy("");
    }
  }

  async function buildSemanticIndex() {
    setBusy("semantic");
    try {
      await indexLibrarySemantic();
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo iniciar el índice semántico.",
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
      const [nextChunks, nextVersions, nextSections, nextQuality] =
        await Promise.all([
          getLibrarySourceChunks(source.id),
          getLibrarySourceVersions(source.id),
          getLibrarySections(source.id),
          getLibraryPageQuality(source.id),
        ]);
      setChunks(nextChunks);
      setVersions(nextVersions);
      setSections(nextSections);
      setPageQuality(nextQuality);
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

  async function answerFeedback(verdict: MemoryFeedbackVerdict) {
    if (!answer) return;
    setBusy("answer-feedback");
    setError("");
    try {
      await sendTeacherResponseFeedback(answer.query_id, verdict);
      setAnswer(await getTeacherQuery(answer.query_id));
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo guardar tu valoración.",
      );
    } finally {
      setBusy("");
    }
  }

  async function locationFeedback(
    locationId: string,
    verdict: MemoryFeedbackVerdict,
    withMapping = false,
  ) {
    if (!answer) return;
    setBusy(`location-${locationId}`);
    setError("");
    try {
      await sendTeacherLocationFeedback(answer.query_id, locationId, {
        verdict,
        ...(withMapping
          ? {
              scan_layout: locationDraft.scan_layout as
                | "single_page"
                | "double_page"
                | "mixed"
                | "unknown",
              region: locationDraft.region as
                | "full"
                | "left"
                | "right"
                | "both"
                | "unknown",
              printed_left_label: locationDraft.printed_left_label || null,
              printed_right_label: locationDraft.printed_right_label || null,
              printed_full_label: locationDraft.printed_full_label || null,
            }
          : {}),
      });
      setAnswer(await getTeacherQuery(answer.query_id));
      setEditingLocationId(null);
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo revisar la ubicación.",
      );
    } finally {
      setBusy("");
    }
  }

  async function searchMemory(event: FormEvent) {
    event.preventDefault();
    setBusy("memory-search");
    try {
      setConcepts(await getPedagogicalConcepts(memorySearch.trim()));
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo consultar la memoria.",
      );
    } finally {
      setBusy("");
    }
  }

  async function openMemoryConcept(conceptId: string) {
    setBusy("memory-detail");
    try {
      const [concept, audit] = await Promise.all([
        getPedagogicalConcept(conceptId),
        getMemoryAudit("concept", conceptId),
      ]);
      setSelectedMemoryConcept(concept);
      setMemoryAudit(audit);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo abrir el concepto.",
      );
    } finally {
      setBusy("");
    }
  }

  async function reviewMemoryItem(
    item: MemoryReviewQueueItem,
    action: "confirm" | "reject" | "unknown" | "postpone",
  ) {
    setBusy(`memory-${item.target_id}`);
    try {
      await reviewPedagogicalMemory(item.target_type, item.target_id, action);
      await refresh();
      if (item.target_type === "concept") {
        await openMemoryConcept(item.target_id);
      }
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo revisar la memoria.",
      );
    } finally {
      setBusy("");
    }
  }

  async function revertAudit(item: MemoryAudit) {
    const reviewId =
      typeof item.after.review_id === "string" ? item.after.review_id : null;
    if (!reviewId) return;
    setBusy("memory-revert");
    try {
      await revertPedagogicalMemoryReview(reviewId);
      if (selectedMemoryConcept) {
        await openMemoryConcept(selectedMemoryConcept.id);
      }
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo deshacer el cambio.",
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

      <section
        className="library-panel core-sources"
        aria-labelledby="core-title"
      >
        <div className="panel-title">
          <div>
            <p className="eyebrow">BASE PEDAGÓGICA PRINCIPAL</p>
            <h2 id="core-title">Colección Herder</h2>
          </div>
          <span>{core?.ready ? "confirmada" : "por confirmar"}</span>
        </div>
        {core?.ready ? (
          <div className="core-source-grid">
            {[core.theory, core.workbook].map((source) =>
              source ? (
                <article key={source.id}>
                  <small>
                    {source.pedagogical_role === "core_theory"
                      ? "Teoría principal"
                      : "Práctica principal"}
                  </small>
                  <strong>{source.display_alias ?? source.name}</strong>
                  <span>
                    Extracción {source.processing_state} · índice semántico{" "}
                    {source.semantic_indexed_chunks > 0
                      ? `${source.semantic_indexed_chunks} vectores`
                      : source.semantic_failed_chunks > 0
                        ? "con errores"
                        : "sin vectores"}
                  </span>
                  <button onClick={() => void openSourceDetail(source.id)}>
                    Revisar fuente
                  </button>
                </article>
              ) : null,
            )}
          </div>
        ) : (
          <div>
            <p>
              Confirma qué libro contiene la teoría y cuál contiene la práctica.
              Los archivos originales no se renombran.
            </p>
            <div className="core-source-grid">
              {core?.candidates.map((candidate) => (
                <article key={candidate.source.id}>
                  <strong>{candidate.source.name}</strong>
                  <span>{candidate.evidence.join(" ")}</span>
                  {candidate.suggested_role ? (
                    <button
                      disabled={busy === "core"}
                      onClick={() =>
                        void assignCoreCandidate(
                          candidate.source,
                          candidate.suggested_role as
                            | "core_theory"
                            | "core_workbook"
                            | "core_answer_key",
                        )
                      }
                    >
                      Confirmar como{" "}
                      {candidate.suggested_role === "core_theory"
                        ? "teoría"
                        : candidate.suggested_role === "core_workbook"
                          ? "práctica"
                          : "solucionario"}
                    </button>
                  ) : null}
                </article>
              ))}
            </div>
          </div>
        )}
      </section>

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
          {answer.failure_reason ? (
            <aside className="warning">
              <strong>La respuesta no pudo completarse normalmente</strong>
              <p>
                Motivo: {answer.failure_reason}. Puedes reintentar sin perder el
                historial de esta consulta.
              </p>
            </aside>
          ) : null}
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
          <section
            className="memory-feedback"
            aria-labelledby="answer-feedback-title"
          >
            <strong id="answer-feedback-title">
              ¿La explicación te resultó correcta?
            </strong>
            <div role="group" aria-label="Valorar explicación">
              {(
                [
                  ["correct", "Correcta"],
                  ["incorrect", "Incorrecta"],
                  ["unknown", "No lo sé"],
                ] as const
              ).map(([verdict, label]) => (
                <button
                  type="button"
                  key={verdict}
                  aria-pressed={answer.response_feedback === verdict}
                  disabled={busy === "answer-feedback"}
                  onClick={() => void answerFeedback(verdict)}
                >
                  {label}
                </button>
              ))}
            </div>
            <small>
              Esta valoración no confirma automáticamente ninguna fuente.
            </small>
          </section>
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
                      {source.evidence_origin === "core"
                        ? "Base Herder principal · "
                        : "Apoyo complementario · "}
                      {source.section ? `${source.section} · ` : ""}
                      {source.public_location ??
                        (source.page_start
                          ? `PDF p. ${source.page_start}`
                          : "ubicación interna")}
                    </span>
                    {source.memory_status ? (
                      <small className={`memory-state ${source.memory_status}`}>
                        {source.memory_status === "user_confirmed"
                          ? "Ubicación confirmada por ti"
                          : source.memory_status === "system_verified"
                            ? "Referencia verificada"
                            : source.memory_status === "rejected"
                              ? "Ubicación rechazada"
                              : "Ubicación pendiente de revisión"}
                      </small>
                    ) : null}
                    <blockquote>{source.snippet}</blockquote>
                    {source.location_id ? (
                      <div className="source-memory-controls">
                        <span>¿La ubicación es correcta?</span>
                        <button
                          type="button"
                          disabled={busy === `location-${source.location_id}`}
                          onClick={() =>
                            void locationFeedback(
                              source.location_id!,
                              "correct",
                            )
                          }
                        >
                          Correcta
                        </button>
                        <button
                          type="button"
                          disabled={busy === `location-${source.location_id}`}
                          onClick={() =>
                            void locationFeedback(
                              source.location_id!,
                              "incorrect",
                            )
                          }
                        >
                          Incorrecta
                        </button>
                        <button
                          type="button"
                          disabled={busy === `location-${source.location_id}`}
                          onClick={() =>
                            void locationFeedback(
                              source.location_id!,
                              "unknown",
                            )
                          }
                        >
                          No lo sé
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setEditingLocationId(source.location_id);
                            setLocationDraft({
                              scan_layout: source.scan_layout,
                              region: source.region,
                              printed_left_label: "",
                              printed_right_label: "",
                              printed_full_label:
                                source.printed_page_label ?? "",
                            });
                          }}
                        >
                          Corregir página
                        </button>
                        {editingLocationId === source.location_id ? (
                          <form
                            className="page-map-editor"
                            onSubmit={(event) => {
                              event.preventDefault();
                              void locationFeedback(
                                source.location_id!,
                                "correct",
                                true,
                              );
                            }}
                          >
                            <label>
                              Escaneo
                              <select
                                value={locationDraft.scan_layout}
                                onChange={(event) =>
                                  setLocationDraft((current) => ({
                                    ...current,
                                    scan_layout: event.target.value,
                                  }))
                                }
                              >
                                <option value="unknown">Sin comprobar</option>
                                <option value="single_page">Una página</option>
                                <option value="double_page">Dos páginas</option>
                                <option value="mixed">Mixto</option>
                              </select>
                            </label>
                            <label>
                              Región
                              <select
                                value={locationDraft.region}
                                onChange={(event) =>
                                  setLocationDraft((current) => ({
                                    ...current,
                                    region: event.target.value,
                                  }))
                                }
                              >
                                <option value="unknown">Sin comprobar</option>
                                <option value="left">Mitad izquierda</option>
                                <option value="right">Mitad derecha</option>
                                <option value="both">Ambas mitades</option>
                                <option value="full">Página completa</option>
                              </select>
                            </label>
                            <label>
                              Página impresa izquierda
                              <input
                                value={locationDraft.printed_left_label}
                                onChange={(event) =>
                                  setLocationDraft((current) => ({
                                    ...current,
                                    printed_left_label: event.target.value,
                                  }))
                                }
                                placeholder="desconocida"
                              />
                            </label>
                            <label>
                              Página impresa derecha
                              <input
                                value={locationDraft.printed_right_label}
                                onChange={(event) =>
                                  setLocationDraft((current) => ({
                                    ...current,
                                    printed_right_label: event.target.value,
                                  }))
                                }
                                placeholder="desconocida"
                              />
                            </label>
                            <button type="submit">Guardar y confirmar</button>
                          </form>
                        ) : null}
                      </div>
                    ) : null}
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
              <div>
                <dt>Modelos</dt>
                <dd>
                  {Object.entries(answer.models)
                    .map(([role, model]) => `${role}: ${model}`)
                    .join(" · ") || "sin generación"}
                </dd>
              </div>
              <div>
                <dt>Caché</dt>
                <dd>
                  {answer.cache_hit ? "plan reutilizado" : "consulta nueva"}
                </dd>
              </div>
            </dl>
            {answer.sources.length ? (
              <ul className="technical-evidence">
                {answer.sources.map((source) => (
                  <li key={source.citation}>
                    {source.citation}: score {source.retrieval_score.toFixed(3)}{" "}
                    · calidad de extracción{" "}
                    {(source.extraction_quality * 100).toFixed(0)} % · rol{" "}
                    {source.content_role} · {source.evidence_origin}
                  </li>
                ))}
              </ul>
            ) : null}
          </details>
        </article>
      ) : null}

      <section
        className="verified-memory library-panel"
        aria-labelledby="memory-title"
      >
        <div className="panel-title">
          <div>
            <p className="eyebrow">MEMORIA DOCUMENTAL CORREGIBLE</p>
            <h2 id="memory-title">Memoria verificada</h2>
          </div>
          <span>{memorySummary?.concepts ?? 0} conceptos</span>
        </div>
        <p>
          Conserva conceptos y ubicaciones en los originales. Las respuestas del
          modelo solo pueden proponer candidatos.
        </p>
        <div className="memory-metrics" aria-label="Estados de memoria">
          <span>
            <strong>{memorySummary?.by_status.user_confirmed ?? 0}</strong>
            confirmados
          </span>
          <span>
            <strong>{memorySummary?.by_status.candidate ?? 0}</strong>
            candidatos
          </span>
          <span>
            <strong>{memorySummary?.by_status.rejected ?? 0}</strong>
            rechazados
          </span>
          <span>
            <strong>{memorySummary?.by_status.conflict ?? 0}</strong>
            conflictos
          </span>
          <span>
            <strong>{memorySummary?.by_status.stale ?? 0}</strong>
            obsoletos
          </span>
        </div>
        <form className="memory-search" onSubmit={searchMemory}>
          <label htmlFor="memory-search">Buscar concepto o alias</label>
          <div>
            <input
              id="memory-search"
              value={memorySearch}
              onChange={(event) => setMemorySearch(event.target.value)}
              placeholder="Akkusativ, acusativo, Perfekt…"
              maxLength={200}
            />
            <button disabled={busy === "memory-search"}>Buscar</button>
          </div>
        </form>
        <div className="memory-layout">
          <div className="concept-atlas" aria-label="Atlas de conceptos">
            {concepts.length ? (
              concepts.map((concept) => (
                <button
                  type="button"
                  key={concept.id}
                  className={
                    selectedMemoryConcept?.id === concept.id ? "selected" : ""
                  }
                  onClick={() => void openMemoryConcept(concept.id)}
                >
                  <span>
                    <strong>
                      {concept.display_name_es ?? concept.canonical_name}
                    </strong>
                    {concept.display_name_de &&
                    concept.display_name_de !== concept.display_name_es ? (
                      <small lang="de">{concept.display_name_de}</small>
                    ) : null}
                  </span>
                  <b className={`memory-state ${concept.status}`}>
                    {concept.status}
                  </b>
                  <small>
                    {concept.category} ·{" "}
                    {Object.values(concept.location_counts).reduce(
                      (total, count) => total + count,
                      0,
                    )}{" "}
                    ubicaciones
                  </small>
                </button>
              ))
            ) : (
              <p className="muted">Todavía no hay conceptos con ese nombre.</p>
            )}
          </div>
          <article className="concept-memory-detail">
            {selectedMemoryConcept ? (
              <>
                <header>
                  <div>
                    <p className="eyebrow">DETALLE DEL CONCEPTO</p>
                    <h3>{selectedMemoryConcept.canonical_name}</h3>
                  </div>
                  <b className={`memory-state ${selectedMemoryConcept.status}`}>
                    {selectedMemoryConcept.status}
                  </b>
                </header>
                <p>
                  {selectedMemoryConcept.description ??
                    selectedMemoryConcept.category}
                </p>
                <dl>
                  <div>
                    <dt>Alias</dt>
                    <dd>
                      {selectedMemoryConcept.aliases
                        .map((alias) => `${alias.text} (${alias.language})`)
                        .join(" · ")}
                    </dd>
                  </div>
                  <div>
                    <dt>Relaciones</dt>
                    <dd>
                      {selectedMemoryConcept.relations
                        .map(
                          (relation) =>
                            `${relation.relation_type}: ${relation.target_name}`,
                        )
                        .join(" · ") || "Sin relaciones confirmadas"}
                    </dd>
                  </div>
                </dl>
                <h4>Ubicaciones documentales</h4>
                <ul className="memory-locations">
                  {selectedMemoryConcept.locations.map((location) => (
                    <li key={location.id}>
                      <strong>{location.public_citation}</strong>
                      <span className={`memory-state ${location.status}`}>
                        {location.status}
                      </span>
                      <small>{location.heading ?? "Sin encabezado"}</small>
                      <q>{location.evidence_snippet}</q>
                    </li>
                  ))}
                </ul>
                <details>
                  <summary>
                    Cambios y correcciones ({memoryAudit.length})
                  </summary>
                  <ol className="memory-audit">
                    {memoryAudit.map((item) => (
                      <li key={item.id}>
                        <span>
                          {item.action} ·{" "}
                          {new Date(item.created_at).toLocaleString("es-ES")}
                        </span>
                        {item.comment ? <small>{item.comment}</small> : null}
                        {typeof item.after.review_id === "string" ? (
                          <button
                            type="button"
                            disabled={busy === "memory-revert"}
                            onClick={() => void revertAudit(item)}
                          >
                            Deshacer
                          </button>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                </details>
              </>
            ) : (
              <div className="empty-inline">
                <h3>Selecciona un concepto</h3>
                <p>Verás alias, relaciones, evidencia y su historial.</p>
              </div>
            )}
          </article>
        </div>
        <details className="memory-review-queue">
          <summary>Cola de revisión ({reviewQueue.length})</summary>
          <p>
            “No lo sé” es neutral: registra la revisión sin confirmar ni
            rechazar.
          </p>
          <div>
            {reviewQueue.map((item) => (
              <article key={`${item.target_type}-${item.target_id}`}>
                <span>
                  <strong>{item.title}</strong>
                  <small>{item.subtitle ?? item.target_type}</small>
                </span>
                <div>
                  {(
                    [
                      ["confirm", "Confirmar"],
                      ["reject", "Rechazar"],
                      ["unknown", "No lo sé"],
                      ["postpone", "Después"],
                    ] as const
                  ).map(([action, label]) => (
                    <button
                      type="button"
                      key={action}
                      disabled={busy === `memory-${item.target_id}`}
                      onClick={() => void reviewMemoryItem(item, action)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </details>
      </section>

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
            <span>{summary?.semantic_index.indexed ?? "—"}</span>
            <small>vectores</small>
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
        <p className="capability-line">
          Modelos locales:{" "}
          {modelRouting?.installed_models.join(", ") || "no disponibles"}
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
          <button
            disabled={Boolean(activeJob) || busy === "semantic"}
            onClick={() => void buildSemanticIndex()}
          >
            Actualizar índice semántico
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
                      <strong>{source.display_alias ?? source.name}</strong>
                      <small>
                        {source.pedagogical_role} · {source.format} ·{" "}
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
                <h2>{selectedSource.display_alias ?? selectedSource.name}</h2>
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
                  <div>
                    <dt>Rol pedagógico</dt>
                    <dd>{selectedSource.pedagogical_role}</dd>
                  </div>
                  <div>
                    <dt>Estado editorial</dt>
                    <dd>{selectedSource.editorial_status}</dd>
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
                    disabled={busy === "sections"}
                    onClick={() => void prepareSections()}
                  >
                    Preparar índice de secciones
                  </button>
                  <button
                    disabled={busy === "pages"}
                    onClick={() => void analyzePages()}
                  >
                    Analizar páginas
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
                <details>
                  <summary>Índice editorial ({sections.length})</summary>
                  {sections.length ? (
                    <ol>
                      {sections.map((section) => (
                        <li key={section.id}>
                          {section.title} · p. {section.page_start}–
                          {section.page_end} · {section.editorial_status}
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p className="muted">
                      Todavía no hay secciones editoriales.
                    </p>
                  )}
                </details>
                <details>
                  <summary>Calidad por página ({pageQuality.length})</summary>
                  {pageQuality.length ? (
                    <ul>
                      {pageQuality
                        .filter((page) => page.quality !== "good")
                        .slice(0, 30)
                        .map((page) => (
                          <li key={page.id}>
                            p. {page.page_number} · {page.quality}
                            {page.warnings.length
                              ? ` · ${page.warnings.join(" ")}`
                              : ""}
                          </li>
                        ))}
                    </ul>
                  ) : (
                    <p className="muted">
                      Ejecuta el análisis por página cuando lo necesites.
                    </p>
                  )}
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
