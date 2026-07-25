"use client";

import type {
  StudyDashboard,
  StudyMissionType,
  StudyNote,
  StudyPath,
  StudyQuestion,
  StudySection,
  StudySession,
  TeacherQuery,
} from "@deutschos/shared";
import type { FormEvent } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  askStudyTeacher,
  createStudyNote,
  createStudyQuestion,
  createStudyWorkbookLink,
  deleteAllStudyData,
  deleteStudyNote,
  deleteStudyQuestion,
  deleteStudySession,
  getStudyDashboard,
  getStudyHistory,
  getStudyNotes,
  getStudyPath,
  getStudyQuestions,
  operationId,
  reviewCanonicalStudyTopic,
  reviewStudyWorkbookLink,
  startStudySession,
  transitionStudySession,
  updateStudyNote,
  updateStudyPosition,
  updateStudyPreferences,
  updateStudyQuestion,
  updateStudyWorkbookLink,
} from "../lib/api";

type View = "dashboard" | "path" | "session" | "history" | "free";

const STATUS_LABELS: Record<string, string> = {
  not_started: "Sin empezar",
  in_progress: "En curso",
  viewed: "Vista",
  needs_review: "Necesita repaso",
  completed_by_user: "Terminada por ti",
  paused: "Pausada",
  active: "Activa",
  planned: "Planificada",
  completed: "Cerrada",
  abandoned: "Abandonada",
};

const MISSION_LABELS: Record<StudyMissionType, string> = {
  automatic: "Automática",
  standard: "Estudio directo",
  laboratory: "Laboratorio",
  frozen_city: "Ciudad helada",
  underwater_exploration: "Expedición submarina",
  space_mission: "Operación espacial",
  mixed: "Sorpresa temática",
};

const WORKBOOK_STATUS_LABELS: Record<string, string> = {
  candidate: "Candidato",
  user_confirmed: "Confirmado por ti",
  rejected: "Rechazado",
  stale: "Pendiente de revisar",
};

const EDITORIAL_STATUS_LABELS: Record<string, string> = {
  system_verified: "Verificado con el índice",
  user_confirmed: "Confirmado por ti",
  candidate: "Pendiente de revisión",
  conflict: "Conflicto",
  stale: "Versión anterior",
  rejected: "Marcado como incorrecto",
  system_suggested: "Sección legacy sugerida",
};

const QUESTION_STATUS_LABELS: Record<string, string> = {
  open: "Abierta",
  clarified: "Aclarada",
  revisit: "Volver a revisar",
  archived: "Archivada",
};

const RESULT_LABELS: Record<string, string> = {
  understood: "Lo he entendido",
  needs_review: "Necesito repasarlo",
  unfinished: "Me he quedado a medias",
  difficult: "Este tema me cuesta",
  continue_next_time: "Continuar la próxima vez",
  change_topic: "Cambiaré de tema",
};

const QUICK_ACTIONS = [
  ["explain", "Explícame esta parte"],
  ["summarize", "Resúmelo brevemente"],
  ["another_example", "Dame otro ejemplo"],
  ["compare_spanish", "Compáralo con el español"],
  ["why_form", "¿Por qué se usa esta forma?"],
  ["laboratory_example", "Ejemplo de laboratorio"],
  ["thematic_example", "Ejemplo temático"],
  ["locate_rule", "Localiza la regla"],
  ["not_understood", "No lo he entendido"],
] as const;

function dateLabel(value: string | null): string {
  if (!value) return "Sin actividad";
  return new Intl.DateTimeFormat("es-ES", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function pageLabel(
  section: Pick<StudySection, "pdf_page_start" | "pdf_page_end">,
) {
  if (!section.pdf_page_start) return "PDF del manual pendiente";
  return !section.pdf_page_end ||
    section.pdf_page_start === section.pdf_page_end
    ? `PDF p. ${section.pdf_page_start}`
    : `PDF pp. ${section.pdf_page_start}–${section.pdf_page_end}`;
}

export function StudyWorkspace() {
  const [dashboard, setDashboard] = useState<StudyDashboard | null>(null);
  const [path, setPath] = useState<StudyPath | null>(null);
  const [history, setHistory] = useState<StudySession[]>([]);
  const [session, setSession] = useState<StudySession | null>(null);
  const [notes, setNotes] = useState<StudyNote[]>([]);
  const [questions, setQuestions] = useState<StudyQuestion[]>([]);
  const [view, setView] = useState<View>("dashboard");
  const [search, setSearch] = useState("");
  const [duration, setDuration] = useState("30");
  const [mission, setMission] = useState<StudyMissionType>("automatic");
  const [noteText, setNoteText] = useState("");
  const [questionText, setQuestionText] = useState("");
  const [teacher, setTeacher] = useState<TeacherQuery | null>(null);
  const [selectedNoteIds, setSelectedNoteIds] = useState<string[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [closeResult, setCloseResult] = useState("needs_review");
  const [historyFilter, setHistoryFilter] = useState("all");
  const [editingWorkbook, setEditingWorkbook] = useState(false);
  const [free, setFree] = useState({
    title: "",
    concept: "",
    objective: "",
    pageStart: "",
    pageEnd: "",
  });
  const [workbook, setWorkbook] = useState({
    pdfPage: "",
    printedPage: "",
    exerciseStart: "",
    exerciseEnd: "",
  });

  const refresh = useCallback(async () => {
    const [nextDashboard, nextPath, nextHistory] = await Promise.all([
      getStudyDashboard(),
      getStudyPath(),
      getStudyHistory(),
    ]);
    setDashboard(nextDashboard);
    setPath(nextPath);
    setHistory(nextHistory);
    const current = nextDashboard.active_session;
    setSession(
      (existing) =>
        current ??
        (existing && nextHistory.some((item) => item.id === existing.id)
          ? existing
          : null),
    );
    if (current) {
      const [nextNotes, nextQuestions] = await Promise.all([
        getStudyNotes(current.id),
        getStudyQuestions(current.id),
      ]);
      setNotes(nextNotes);
      setQuestions(nextQuestions);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      refresh().catch((cause: unknown) =>
        setError(
          cause instanceof Error
            ? cause.message
            : "No se pudo cargar el modo estudio.",
        ),
      );
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const filteredSections = useMemo(() => {
    const folded = search.trim().toLocaleLowerCase("es");
    if (!folded) return path?.sections ?? [];
    return (path?.sections ?? []).filter((item) =>
      `${item.theme_number ? `Tema ${item.theme_number}` : ""} ${item.title} ${item.title_de ?? ""} ${item.topic ?? ""} ${item.printed_page_start ?? ""} ${item.outline.map((node) => `${node.title_es ?? ""} ${node.title_de ?? ""}`).join(" ")}`
        .toLocaleLowerCase("es")
        .includes(folded),
    );
  }, [path, search]);

  const filteredHistory = useMemo(() => {
    if (historyFilter === "needs_review") {
      return history.filter(
        (item) => item.subjective_result === "needs_review",
      );
    }
    if (historyFilter === "completed") {
      return history.filter((item) => item.status === "completed");
    }
    if (historyFilter === "in_progress") {
      return history.filter((item) =>
        ["active", "paused", "planned"].includes(item.status),
      );
    }
    if (historyFilter === "laboratory") {
      return history.filter((item) => item.mission.type === "laboratory");
    }
    if (historyFilter === "thematic") {
      return history.filter((item) => item.mission.type !== "standard");
    }
    return history;
  }, [history, historyFilter]);

  const currentWorkbookLink = useMemo(
    () =>
      path?.sections.find(
        (item) => item.stable_key === session?.section_stable_key,
      )?.workbook_link ?? null,
    [path, session?.section_stable_key],
  );

  const run = async (name: string, action: () => Promise<void>) => {
    setBusy(name);
    setError("");
    try {
      await action();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "La operación no se pudo completar.",
      );
    } finally {
      setBusy("");
    }
  };

  const loadSessionData = async (next: StudySession) => {
    setSession(next);
    const [nextNotes, nextQuestions] = await Promise.all([
      getStudyNotes(next.id),
      getStudyQuestions(next.id),
    ]);
    setNotes(nextNotes);
    setQuestions(nextQuestions);
    setView("session");
  };

  const startSection = (section: StudySection) =>
    run("start", async () => {
      const next = await startStudySession({
        operation_id: operationId("start-guided"),
        kind: "guided",
        section_id: section.id,
        planned_minutes: duration ? Number(duration) : null,
        mission_type: mission,
        start_origin: "section_picker",
        activate: true,
      });
      await loadSessionData(next);
      await refresh();
    });

  const reviewTopic = (
    section: StudySection,
    action: "confirm" | "incorrect" | "unknown",
  ) => {
    if (!section.theme_number) return;
    run(`route-${action}-${section.theme_number}`, async () => {
      await reviewCanonicalStudyTopic(section.theme_number!, {
        operation_id: operationId(`route-${action}`),
        action,
      });
      await refresh();
    });
  };

  const reviewTopicPages = (section: StudySection) => {
    if (!section.theme_number) return;
    const value = window.prompt(
      "Página PDF verificada del manual (deja vacío para cancelar)",
      section.pdf_page_start ? String(section.pdf_page_start) : "",
    );
    if (!value) return;
    const manualPage = Number(value);
    if (!Number.isInteger(manualPage) || manualPage < 1) return;
    run(`route-pages-${section.theme_number}`, async () => {
      await reviewCanonicalStudyTopic(section.theme_number!, {
        operation_id: operationId("route-pages"),
        action: "correct",
        manual_pdf_start: manualPage,
        manual_pdf_end: manualPage,
        manual_scan_layout: "unknown",
        manual_region: "unknown",
        comment: "Calibración manual desde la ruta de estudio.",
      });
      await refresh();
    });
  };

  const transition = (action: "pause" | "resume" | "complete" | "abandon") =>
    session &&
    run(action, async () => {
      const next = await transitionStudySession(session.id, {
        operation_id: operationId(`study-${action}`),
        action,
        subjective_result: action === "complete" ? closeResult : null,
        final_pdf_page: session.current_pdf_page,
        next_action:
          action === "complete" && closeResult === "needs_review"
            ? "Repasar esta sección"
            : null,
      });
      setSession(next);
      await refresh();
      if (["complete", "abandon"].includes(action)) setView("dashboard");
    });

  const savePosition = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!session) return;
    const data = new FormData(event.currentTarget);
    run("position", async () => {
      const next = await updateStudyPosition(session.id, {
        operation_id: operationId("study-position"),
        current_pdf_page: Number(data.get("pdfPage")),
        printed_page_label: String(data.get("printedPage") || "") || null,
      });
      setSession(next);
      await refresh();
    });
  };

  const addNote = (event: FormEvent) => {
    event.preventDefault();
    if (!session || !noteText.trim()) return;
    run("note", async () => {
      await createStudyNote({
        operation_id: operationId("study-note"),
        session_id: session.id,
        source_id: session.source_id,
        section_stable_key: session.section_stable_key,
        concept_name: session.concept_name,
        pdf_page: session.current_pdf_page,
        text: noteText,
      });
      setNoteText("");
      setNotes(await getStudyNotes(session.id));
    });
  };

  const addQuestion = (event: FormEvent) => {
    event.preventDefault();
    if (!session || !questionText.trim()) return;
    run("question", async () => {
      await createStudyQuestion({
        operation_id: operationId("study-question"),
        session_id: session.id,
        source_id: session.source_id,
        section_stable_key: session.section_stable_key,
        concept_name: session.concept_name,
        pdf_page: session.current_pdf_page,
        question: questionText,
      });
      setQuestionText("");
      setQuestions(await getStudyQuestions(session.id));
      await refresh();
    });
  };

  const askTeacher = (action: (typeof QUICK_ACTIONS)[number][0]) => {
    if (!session) return;
    run("teacher", async () => {
      setTeacher(
        await askStudyTeacher(session.id, {
          action,
          include_note_ids: selectedNoteIds,
        }),
      );
    });
  };

  const editNote = (item: StudyNote) => {
    const nextText = window.prompt("Editar nota privada", item.text);
    if (!nextText?.trim() || nextText === item.text) return;
    run("edit-note", async () => {
      await updateStudyNote(item.id, {
        operation_id: operationId("update-note"),
        session_id: item.session_id,
        source_id: item.source_id,
        section_stable_key: item.section_stable_key,
        concept_name: item.concept_name,
        pdf_page: item.pdf_page,
        text: nextText,
      });
      if (session) setNotes(await getStudyNotes(session.id));
    });
  };

  const toggleChecklist = (stepId: string, completed: boolean) => {
    if (!session) return;
    const checklist = (session.plan.steps ?? []).map((step) => ({
      id: step.id,
      completed:
        step.id === stepId
          ? completed
          : session.checklist.some(
              (entry) =>
                typeof entry === "object" &&
                entry !== null &&
                "id" in entry &&
                "completed" in entry &&
                entry.id === step.id &&
                entry.completed === true,
            ),
    }));
    run("checklist", async () => {
      const next = await updateStudyPosition(session.id, {
        operation_id: operationId("study-checklist"),
        current_pdf_page: session.current_pdf_page,
        printed_page_label: session.printed_page_label,
        checklist,
      });
      setSession(next);
      await refresh();
    });
  };

  const checklistCompleted = (stepId: string) =>
    session?.checklist.some(
      (entry) =>
        typeof entry === "object" &&
        entry !== null &&
        "id" in entry &&
        "completed" in entry &&
        entry.id === stepId &&
        entry.completed === true,
    ) ?? false;

  const addWorkbook = (event: FormEvent) => {
    event.preventDefault();
    if (!session?.source_id || !session.section_stable_key || !workbook.pdfPage)
      return;
    run("workbook", async () => {
      const fields = {
        operation_id: operationId("study-workbook"),
        workbook_pdf_page: Number(workbook.pdfPage),
        printed_page_label: workbook.printedPage || null,
        exercise_start: workbook.exerciseStart || null,
        exercise_end: workbook.exerciseEnd || null,
        region: "unknown",
      };
      if (currentWorkbookLink) {
        await updateStudyWorkbookLink(currentWorkbookLink.id, fields);
      } else {
        await createStudyWorkbookLink({
          ...fields,
          theory_source_id: session.source_id,
          theory_section_stable_key: session.section_stable_key,
        });
      }
      setWorkbook({
        pdfPage: "",
        printedPage: "",
        exerciseStart: "",
        exerciseEnd: "",
      });
      setEditingWorkbook(false);
      await refresh();
    });
  };

  const editWorkbook = () => {
    if (!currentWorkbookLink) return;
    setWorkbook({
      pdfPage: String(currentWorkbookLink.workbook_pdf_page),
      printedPage: currentWorkbookLink.printed_page_label ?? "",
      exerciseStart: currentWorkbookLink.exercise_start ?? "",
      exerciseEnd: currentWorkbookLink.exercise_end ?? "",
    });
    setEditingWorkbook(true);
  };

  const reviewWorkbook = (
    action: "confirm" | "reject" | "unknown" | "revert",
  ) => {
    if (!currentWorkbookLink) return;
    run(`workbook-${action}`, async () => {
      await reviewStudyWorkbookLink(currentWorkbookLink.id, action);
      await refresh();
    });
  };

  const startFree = (event: FormEvent) => {
    event.preventDefault();
    if (!free.title.trim()) return;
    run("free", async () => {
      const next = await startStudySession({
        operation_id: operationId("start-free"),
        kind: "free",
        section_title: free.title,
        concept_name: free.concept || free.title,
        objective: free.objective || `Estudiar libremente: ${free.title}.`,
        source_id: path?.source_id ?? null,
        source_name: path?.source_name ?? null,
        pdf_page_start: free.pageStart ? Number(free.pageStart) : null,
        pdf_page_end: free.pageEnd ? Number(free.pageEnd) : null,
        current_pdf_page: free.pageStart ? Number(free.pageStart) : null,
        planned_minutes: duration ? Number(duration) : null,
        mission_type: mission,
        start_origin: "manual_page",
        activate: true,
      });
      await loadSessionData(next);
      await refresh();
    });
  };

  if (!dashboard || !path) {
    return (
      <section className="study-shell" aria-busy={!error}>
        <p className="eyebrow">MODO ESTUDIO</p>
        <h1>
          {error
            ? "No se pudo abrir el recorrido"
            : "Preparando tu ruta Herder…"}
        </h1>
        {error ? (
          <p className="study-error" role="alert">
            {error}
          </p>
        ) : (
          <div className="study-loading" />
        )}
      </section>
    );
  }

  return (
    <section className="study-shell">
      <header className="study-heading">
        <div>
          <p className="eyebrow">HERDER · ESTUDIO GUIADO</p>
          <h1>
            Tu libro, tu ritmo, <em>un camino claro.</em>
          </h1>
          <p>
            La ruta sigue el manual. Tú decides cuándo continuar, repasar o
            cambiar de tema.
          </p>
        </div>
        <div className="study-count">
          <strong>{dashboard.total_sections}</strong>
          <span>temas disponibles</span>
        </div>
      </header>

      <nav className="study-tabs" aria-label="Secciones del modo estudio">
        {(
          [
            ["dashboard", "Inicio"],
            ["path", "Ruta Herder"],
            ["session", "Sesión"],
            ["history", "Historial"],
            ["free", "Estudio libre"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            className={view === id ? "active" : ""}
            onClick={() => setView(id)}
            type="button"
          >
            {label}
          </button>
        ))}
      </nav>

      {error ? (
        <p className="study-error" role="alert">
          {error}
        </p>
      ) : null}

      {view === "dashboard" ? (
        <div className="study-dashboard">
          {dashboard.active_session ? (
            <article className="continue-card">
              <div>
                <p className="eyebrow">CONTINUAR ESTUDIANDO</p>
                <h2>{dashboard.active_session.section_title}</h2>
                <p>{dashboard.recommendation.reason}</p>
                <div className="study-meta">
                  <span>{dashboard.active_session.source_name}</span>
                  <span>
                    PDF p.{" "}
                    {dashboard.active_session.current_pdf_page ?? "sin indicar"}
                  </span>
                  <span>
                    {dashboard.active_session.printed_page_label
                      ? `Libro p. ${dashboard.active_session.printed_page_label}`
                      : "Libro sin página identificada"}
                  </span>
                  <span>{STATUS_LABELS[dashboard.active_session.status]}</span>
                  <span>
                    Última actividad:{" "}
                    {dateLabel(dashboard.active_session.updated_at)}
                  </span>
                  <span>
                    {currentWorkbookLink
                      ? `Workbook PDF p. ${currentWorkbookLink.workbook_pdf_page}`
                      : "Workbook sin vincular"}
                  </span>
                  <span>
                    {notes.length} nota(s) · {questions.length} duda(s)
                  </span>
                </div>
              </div>
              <button
                className="study-primary"
                onClick={() => loadSessionData(dashboard.active_session!)}
                type="button"
              >
                Continuar sesión <span>→</span>
              </button>
            </article>
          ) : (
            <article className="continue-card empty-study">
              <div>
                <p className="eyebrow">EMPIEZA TU RECORRIDO CON HERDER</p>
                <h2>Todavía no has iniciado tu recorrido de estudio.</h2>
                <p>
                  Empieza desde el principio, elige una sección o crea una
                  sesión libre.
                </p>
              </div>
              <div className="study-actions">
                <button
                  className="study-primary"
                  onClick={() =>
                    path.sections[0] && startSection(path.sections[0])
                  }
                  disabled={busy === "start"}
                  type="button"
                >
                  Empezar con Herder
                </button>
                <button onClick={() => setView("path")} type="button">
                  Elegir una sección
                </button>
                <button onClick={() => setView("free")} type="button">
                  Crear sesión libre
                </button>
              </div>
            </article>
          )}

          <div className="study-grid">
            <article>
              <p className="eyebrow">SIGUIENTE PASO</p>
              <h3>{dashboard.recommendation.reason}</h3>
              <button onClick={() => setView("path")} type="button">
                Ver ruta completa →
              </button>
            </article>
            <article>
              <p className="eyebrow">DUDAS ABIERTAS</p>
              <strong className="study-big-number">
                {dashboard.open_questions}
              </strong>
              <p>
                No cambian tu progreso evaluativo. Solo te ayudan a recordar qué
                revisar.
              </p>
            </article>
            <article>
              <p className="eyebrow">PRESENTACIÓN</p>
              <label>
                Temática preferida
                <select
                  value={dashboard.preferences.mission_preference}
                  onChange={(event) =>
                    run("preference", async () => {
                      await updateStudyPreferences(
                        event.target.value as StudyMissionType,
                      );
                      await refresh();
                    })
                  }
                >
                  {Object.entries(MISSION_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <small>Es opcional y nunca altera la gramática del manual.</small>
            </article>
          </div>
        </div>
      ) : null}

      {view === "path" ? (
        <div className="study-path">
          <div className="study-toolbar">
            <div>
              <p className="eyebrow">RUTA PRINCIPAL</p>
              <h2>{path.source_name}</h2>
            </div>
            <label>
              Buscar concepto
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Acusativo, pronombres…"
              />
            </label>
          </div>
          <div className="study-start-settings">
            <label>
              Duración orientativa
              <select
                value={duration}
                onChange={(event) => setDuration(event.target.value)}
              >
                <option value="20">20 min</option>
                <option value="30">30 min</option>
                <option value="45">45 min</option>
                <option value="60">60 min</option>
                <option value="">Sin límite</option>
              </select>
            </label>
            <label>
              Presentación
              <select
                value={mission}
                onChange={(event) =>
                  setMission(event.target.value as StudyMissionType)
                }
              >
                {Object.entries(MISSION_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <ol className="herder-path-list">
            {filteredSections.map((item) => (
              <li key={item.id}>
                <span className="path-order">
                  {item.theme_number ? `Tema ${item.theme_number}` : item.order}
                </span>
                <div>
                  <small>{item.topic ?? "Ruta Herder"}</small>
                  <h3>{item.title_es ?? item.title}</h3>
                  {item.title_de ? <p lang="de">{item.title_de}</p> : null}
                  <p>
                    {item.printed_page_start
                      ? `Libro ${
                          item.printed_page_end &&
                          item.printed_page_end !== item.printed_page_start
                            ? `pp. ${item.printed_page_start}–${item.printed_page_end}`
                            : `p. ${item.printed_page_start}`
                        }`
                      : "Página impresa pendiente"}
                    {` · ${pageLabel(item)}`}
                  </p>
                  <div className="study-meta">
                    <span>{STATUS_LABELS[item.practical_status]}</span>
                    <span>
                      {EDITORIAL_STATUS_LABELS[item.editorial_status] ??
                        "Estado editorial desconocido"}
                    </span>
                    {item.open_questions ? (
                      <span>{item.open_questions} duda(s)</span>
                    ) : null}
                    {item.last_activity_at ? (
                      <span>
                        Última sesión: {dateLabel(item.last_activity_at)}
                      </span>
                    ) : null}
                  </div>
                  {item.outline.length ? (
                    <details className="study-outline">
                      <summary>Ver {item.outline.length} subapartados</summary>
                      <ol>
                        {item.outline.map((node) => (
                          <li
                            className={`outline-${node.hierarchy_level}`}
                            key={node.id}
                          >
                            <span>
                              {node.local_number
                                ? `${node.local_number}. `
                                : ""}
                              {node.title_es ?? node.title_de}
                            </span>
                            <small>
                              {node.printed_page
                                ? `Libro p. ${node.printed_page}`
                                : "Sin página propia"}
                              {node.manual_pdf_page
                                ? ` · PDF p. ${node.manual_pdf_page}${
                                    node.manual_scan_layout === "double_page"
                                      ? " · escaneo doble"
                                      : ""
                                  }${
                                    node.manual_region === "unknown"
                                      ? " · mitad pendiente"
                                      : ""
                                  }`
                                : ""}
                            </small>
                          </li>
                        ))}
                      </ol>
                    </details>
                  ) : null}
                </div>
                <div className="path-action">
                  <button
                    onClick={() => startSection(item)}
                    disabled={Boolean(busy)}
                    type="button"
                  >
                    {item.practical_status === "not_started"
                      ? "Estudiar"
                      : "Abrir"}
                  </button>
                  {item.theme_number ? (
                    <>
                      <button
                        className="secondary"
                        onClick={() => reviewTopicPages(item)}
                        disabled={Boolean(busy)}
                        type="button"
                      >
                        Revisar páginas
                      </button>
                      <button
                        className="secondary"
                        onClick={() => reviewTopic(item, "confirm")}
                        disabled={Boolean(busy)}
                        type="button"
                      >
                        Confirmar título
                      </button>
                      <button
                        className="secondary"
                        onClick={() => reviewTopic(item, "incorrect")}
                        disabled={Boolean(busy)}
                        type="button"
                      >
                        Título incorrecto
                      </button>
                      <button
                        className="secondary"
                        onClick={() => reviewTopic(item, "unknown")}
                        disabled={Boolean(busy)}
                        type="button"
                      >
                        No lo sé
                      </button>
                    </>
                  ) : null}
                  {item.workbook_link ? (
                    <small>
                      Workbook PDF p. {item.workbook_link.workbook_pdf_page}
                    </small>
                  ) : (
                    <small>Sin práctica vinculada</small>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {view === "session" ? (
        session ? (
          <div className="study-session-layout">
            <div className="study-session-main">
              <article className="session-hero">
                <div>
                  <p className="eyebrow">
                    SESIÓN {STATUS_LABELS[session.status]?.toUpperCase()}
                  </p>
                  <h2>{session.section_title}</h2>
                  <p>{session.objective}</p>
                </div>
                <div className="session-reference">
                  <strong>Lee esta sección en tu libro Herder.</strong>
                  <span>{session.source_name}</span>
                  <span>
                    PDF{" "}
                    {session.pdf_page_start === session.pdf_page_end
                      ? `p. ${session.pdf_page_start}`
                      : `pp. ${session.pdf_page_start ?? "?"}–${session.pdf_page_end ?? "?"}`}
                  </span>
                  <span>
                    {session.printed_page_label
                      ? `Libro p. ${session.printed_page_label}`
                      : "La página del libro todavía no está identificada."}
                  </span>
                  <a href="/library">Abrir detalle de la fuente →</a>
                </div>
              </article>

              <article className={`mission-card ${session.mission.type}`}>
                <p className="eyebrow">{session.mission.label.toUpperCase()}</p>
                <h3>{session.mission.brief}</h3>
                {session.mission.example_de ? (
                  <blockquote>
                    <strong lang="de">{session.mission.example_de}</strong>
                    <span>{session.mission.example_es}</span>
                  </blockquote>
                ) : (
                  <p>Sin marco narrativo. Estudio directo del contenido.</p>
                )}
              </article>

              <article className="session-plan">
                <p className="eyebrow">PLAN LIGERO</p>
                <div>
                  {session.plan.steps?.map((step, index) => (
                    <label key={step.id}>
                      <input
                        type="checkbox"
                        checked={checklistCompleted(step.id)}
                        onChange={(event) =>
                          toggleChecklist(step.id, event.target.checked)
                        }
                        disabled={busy === "checklist"}
                      />
                      <b>{index + 1}</b>
                      <span>{step.label}</span>
                    </label>
                  ))}
                </div>
                <small>
                  {session.planned_minutes
                    ? `${session.planned_minutes} minutos orientativos; la sesión no termina automáticamente.`
                    : "Sin duración fija."}
                </small>
              </article>

              <article className="teacher-actions">
                <p className="eyebrow">PROFESOR CONTEXTUAL</p>
                <h3>Pregunta sin salir de la sesión</h3>
                <div>
                  {QUICK_ACTIONS.map(([action, label]) => (
                    <button
                      key={action}
                      onClick={() => askTeacher(action)}
                      disabled={busy === "teacher"}
                      type="button"
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <p className="privacy-note">
                  Tus notas no se envían. Marca una nota abajo solo si quieres
                  usarla explícitamente.
                </p>
                {teacher ? (
                  <section className="inline-teacher-answer">
                    <strong>Explicación verificada</strong>
                    <p>{teacher.answer.direct_answer}</p>
                    {teacher.answer.examples.map((example) => (
                      <p key={example.german}>
                        <b lang="de">{example.german}</b>
                        {example.spanish ? ` — ${example.spanish}` : ""}
                      </p>
                    ))}
                  </section>
                ) : null}
              </article>

              <article className="workbook-card">
                <p className="eyebrow">PRÁCTICA RELACIONADA</p>
                {currentWorkbookLink && !editingWorkbook ? (
                  <div className="workbook-details">
                    <h3>Herder · Ejercicios y soluciones</h3>
                    <p>
                      PDF p. {currentWorkbookLink.workbook_pdf_page}
                      {currentWorkbookLink.printed_page_label
                        ? ` · libro p. ${currentWorkbookLink.printed_page_label}`
                        : ""}
                    </p>
                    <p>
                      {currentWorkbookLink.exercise_start
                        ? `Ejercicios ${currentWorkbookLink.exercise_start}${
                            currentWorkbookLink.exercise_end
                              ? `–${currentWorkbookLink.exercise_end}`
                              : ""
                          }`
                        : "Ejercicios todavía sin indicar"}
                    </p>
                    <p>
                      Estado:{" "}
                      <strong>
                        {WORKBOOK_STATUS_LABELS[currentWorkbookLink.status]}
                      </strong>
                    </p>
                    <div className="workbook-actions">
                      <button onClick={editWorkbook} type="button">
                        Corregir vínculo
                      </button>
                      {currentWorkbookLink.status !== "user_confirmed" ? (
                        <button
                          onClick={() => reviewWorkbook("confirm")}
                          type="button"
                        >
                          Confirmar
                        </button>
                      ) : null}
                      {currentWorkbookLink.status !== "rejected" ? (
                        <button
                          onClick={() => reviewWorkbook("reject")}
                          type="button"
                        >
                          Rechazar
                        </button>
                      ) : null}
                      <button
                        onClick={() => reviewWorkbook("unknown")}
                        type="button"
                      >
                        No lo sé
                      </button>
                      {currentWorkbookLink.reviewed_at ? (
                        <button
                          onClick={() => reviewWorkbook("revert")}
                          type="button"
                        >
                          Revertir revisión
                        </button>
                      ) : null}
                    </div>
                    <small>
                      Las soluciones permanecen ocultas y no se consultan
                      automáticamente.
                    </small>
                  </div>
                ) : (
                  <>
                    <h3>
                      {currentWorkbookLink
                        ? "Corrige la referencia de práctica"
                        : "Todavía no hemos identificado los ejercicios de esta sección."}
                    </h3>
                    <p>
                      {currentWorkbookLink
                        ? "La corrección queda auditada y no cambia la teoría principal."
                        : "Puedes continuar sin workbook o registrar una relación manual."}
                    </p>
                    <form onSubmit={addWorkbook} className="workbook-form">
                      <label>
                        PDF p.
                        <input
                          required
                          inputMode="numeric"
                          value={workbook.pdfPage}
                          onChange={(event) =>
                            setWorkbook({
                              ...workbook,
                              pdfPage: event.target.value,
                            })
                          }
                        />
                      </label>
                      <label>
                        Libro p.
                        <input
                          value={workbook.printedPage}
                          onChange={(event) =>
                            setWorkbook({
                              ...workbook,
                              printedPage: event.target.value,
                            })
                          }
                        />
                      </label>
                      <label>
                        Ejercicio inicial
                        <input
                          value={workbook.exerciseStart}
                          onChange={(event) =>
                            setWorkbook({
                              ...workbook,
                              exerciseStart: event.target.value,
                            })
                          }
                        />
                      </label>
                      <label>
                        Ejercicio final
                        <input
                          value={workbook.exerciseEnd}
                          onChange={(event) =>
                            setWorkbook({
                              ...workbook,
                              exerciseEnd: event.target.value,
                            })
                          }
                        />
                      </label>
                      <button type="submit">
                        {currentWorkbookLink
                          ? "Guardar corrección"
                          : "Guardar como candidata"}
                      </button>
                      {currentWorkbookLink ? (
                        <button
                          onClick={() => setEditingWorkbook(false)}
                          type="button"
                        >
                          Cancelar
                        </button>
                      ) : null}
                    </form>
                  </>
                )}
              </article>
            </div>

            <aside className="study-session-aside">
              <article>
                <p className="eyebrow">POSICIÓN</p>
                <form onSubmit={savePosition}>
                  <label>
                    PDF p.
                    <input
                      name="pdfPage"
                      type="number"
                      min={session.pdf_page_start ?? 1}
                      max={session.pdf_page_end ?? undefined}
                      defaultValue={session.current_pdf_page ?? ""}
                      required
                    />
                  </label>
                  <label>
                    Libro p.
                    <input
                      name="printedPage"
                      defaultValue={session.printed_page_label ?? ""}
                    />
                  </label>
                  <button type="submit" disabled={Boolean(busy)}>
                    Guardar posición
                  </button>
                </form>
              </article>
              <article>
                <p className="eyebrow">NOTAS PRIVADAS</p>
                <form onSubmit={addNote}>
                  <textarea
                    value={noteText}
                    onChange={(event) => setNoteText(event.target.value)}
                    placeholder="Escribe algo que quieras recordar…"
                  />
                  <button type="submit">Guardar nota</button>
                </form>
                <div className="personal-items">
                  {notes.map((item) => (
                    <label key={item.id}>
                      <input
                        type="checkbox"
                        checked={selectedNoteIds.includes(item.id)}
                        onChange={(event) =>
                          setSelectedNoteIds((current) =>
                            event.target.checked
                              ? [...current, item.id]
                              : current.filter((id) => id !== item.id),
                          )
                        }
                      />
                      <span>
                        {item.text}
                        <small>Usar como contexto explícito</small>
                      </span>
                      <span className="personal-actions">
                        <button onClick={() => editNote(item)} type="button">
                          Editar
                        </button>
                        <button
                          aria-label="Eliminar nota"
                          onClick={() => {
                            if (window.confirm("¿Borrar esta nota privada?"))
                              run("delete-note", async () => {
                                await deleteStudyNote(item.id);
                                setNotes(await getStudyNotes(session.id));
                              });
                          }}
                          type="button"
                        >
                          Eliminar
                        </button>
                      </span>
                    </label>
                  ))}
                </div>
              </article>
              <article>
                <p className="eyebrow">DUDAS</p>
                <form onSubmit={addQuestion}>
                  <textarea
                    value={questionText}
                    onChange={(event) => setQuestionText(event.target.value)}
                    placeholder="¿Qué quieres revisar después?"
                  />
                  <button type="submit">Guardar duda</button>
                </form>
                <div className="personal-items">
                  {questions.map((item) => (
                    <div key={item.id}>
                      <span>
                        {item.question}
                        <small>{QUESTION_STATUS_LABELS[item.status]}</small>
                      </span>
                      <span className="personal-actions">
                        <button
                          onClick={() =>
                            run("question-status", async () => {
                              await updateStudyQuestion(item.id, {
                                operation_id: operationId("question-status"),
                                status:
                                  item.status === "clarified"
                                    ? "open"
                                    : "clarified",
                              });
                              setQuestions(await getStudyQuestions(session.id));
                            })
                          }
                          type="button"
                        >
                          {item.status === "clarified" ? "Reabrir" : "Aclarada"}
                        </button>
                        <button
                          onClick={() => {
                            if (window.confirm("¿Borrar esta duda?"))
                              run("delete-question", async () => {
                                await deleteStudyQuestion(item.id);
                                setQuestions(
                                  await getStudyQuestions(session.id),
                                );
                                await refresh();
                              });
                          }}
                          type="button"
                        >
                          Eliminar
                        </button>
                      </span>
                    </div>
                  ))}
                </div>
              </article>
              <article className="session-controls">
                <p className="eyebrow">CONTROL</p>
                {session.status === "active" ? (
                  <button onClick={() => transition("pause")} type="button">
                    Pausar sesión
                  </button>
                ) : session.status === "paused" ? (
                  <button
                    className="study-primary"
                    onClick={() => transition("resume")}
                    type="button"
                  >
                    Continuar sesión
                  </button>
                ) : null}
                {["active", "paused", "planned"].includes(session.status) ? (
                  <>
                    <label>
                      ¿Cómo ha ido?
                      <select
                        value={closeResult}
                        onChange={(event) => setCloseResult(event.target.value)}
                      >
                        <option value="understood">Lo he entendido</option>
                        <option value="needs_review">Necesito repasarlo</option>
                        <option value="unfinished">
                          Me he quedado a medias
                        </option>
                        <option value="difficult">Este tema me cuesta</option>
                        <option value="continue_next_time">
                          Continuar la próxima vez
                        </option>
                        <option value="change_topic">Cambiaré de tema</option>
                      </select>
                    </label>
                    <button
                      onClick={() => transition("complete")}
                      type="button"
                    >
                      Cerrar sesión
                    </button>
                    <button
                      className="text-danger"
                      onClick={() => transition("abandon")}
                      type="button"
                    >
                      Abandonar
                    </button>
                  </>
                ) : (
                  <p>Esta sesión ya está cerrada.</p>
                )}
              </article>
            </aside>
          </div>
        ) : (
          <div className="empty-study">
            <h2>No hay una sesión seleccionada.</h2>
            <button onClick={() => setView("path")} type="button">
              Elegir una sección
            </button>
          </div>
        )
      ) : null}

      {view === "history" ? (
        <div className="study-history">
          <div className="history-toolbar">
            <div>
              <p className="eyebrow">HISTORIAL LOCAL</p>
              <h2>Tu recorrido reciente</h2>
            </div>
            <label>
              Filtrar
              <select
                value={historyFilter}
                onChange={(event) => setHistoryFilter(event.target.value)}
              >
                <option value="all">Reciente</option>
                <option value="needs_review">Necesita repaso</option>
                <option value="completed">Completadas</option>
                <option value="in_progress">En curso</option>
                <option value="laboratory">Laboratorio</option>
                <option value="thematic">Temáticas</option>
              </select>
            </label>
          </div>
          {filteredHistory.length ? (
            filteredHistory.map((item) => (
              <article key={item.id}>
                <div>
                  <small>{dateLabel(item.started_at)}</small>
                  <h3>{item.section_title}</h3>
                  <p>
                    {item.source_name ?? "Estudio libre"} ·{" "}
                    {item.current_pdf_page
                      ? `PDF p. ${item.current_pdf_page}`
                      : "sin página"}
                  </p>
                  {item.subjective_result ? (
                    <small>
                      Resultado personal:{" "}
                      {RESULT_LABELS[item.subjective_result]}
                    </small>
                  ) : null}
                </div>
                <div>
                  <span className="state">{STATUS_LABELS[item.status]}</span>
                  <span>{item.mission.label}</span>
                  <span>
                    {Math.round(item.active_seconds / 60)} min activos
                  </span>
                </div>
                <div className="history-actions">
                  <button onClick={() => loadSessionData(item)} type="button">
                    Ver sesión
                  </button>
                  <button
                    className="text-danger"
                    onClick={() => {
                      if (
                        window.confirm(
                          "¿Borrar esta sesión y sus notas y dudas asociadas?",
                        )
                      )
                        run("delete-session", async () => {
                          await deleteStudySession(item.id);
                          if (session?.id === item.id) setSession(null);
                          await refresh();
                        });
                    }}
                    type="button"
                  >
                    Borrar
                  </button>
                </div>
              </article>
            ))
          ) : (
            <p>No hay sesiones que coincidan con este filtro.</p>
          )}
          {history.length ? (
            <div className="history-danger-zone">
              <div>
                <strong>Borrar datos del modo estudio</strong>
                <p>El progreso evaluado y la biblioteca no se modificarán.</p>
              </div>
              <button
                className="text-danger"
                onClick={() => {
                  const confirmation = window.prompt(
                    "Escribe BORRAR para eliminar sesiones, notas, dudas y vínculos de estudio.",
                  );
                  if (confirmation === "BORRAR")
                    run("delete-all", async () => {
                      await deleteAllStudyData();
                      setSession(null);
                      setNotes([]);
                      setQuestions([]);
                      await refresh();
                    });
                }}
                type="button"
              >
                Borrar todo el modo estudio
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {view === "free" ? (
        <form className="free-study" onSubmit={startFree}>
          <div>
            <p className="eyebrow">ESTUDIO LIBRE</p>
            <h2>Estudia un concepto sin alterar el orden Herder</h2>
            <p>
              La sesión quedará en tu historial, pero no moverá la siguiente
              sección sugerida.
            </p>
          </div>
          <label>
            Título de la sesión
            <input
              required
              value={free.title}
              onChange={(event) =>
                setFree({ ...free, title: event.target.value })
              }
              placeholder="Por ejemplo: repaso del acusativo"
            />
          </label>
          <label>
            Concepto
            <input
              value={free.concept}
              onChange={(event) =>
                setFree({ ...free, concept: event.target.value })
              }
              placeholder="Akkusativ"
            />
          </label>
          <label className="full">
            Objetivo
            <textarea
              value={free.objective}
              onChange={(event) =>
                setFree({ ...free, objective: event.target.value })
              }
            />
          </label>
          <label>
            PDF desde
            <input
              type="number"
              min="1"
              value={free.pageStart}
              onChange={(event) =>
                setFree({ ...free, pageStart: event.target.value })
              }
            />
          </label>
          <label>
            PDF hasta
            <input
              type="number"
              min="1"
              value={free.pageEnd}
              onChange={(event) =>
                setFree({ ...free, pageEnd: event.target.value })
              }
            />
          </label>
          <label>
            Duración
            <select
              value={duration}
              onChange={(event) => setDuration(event.target.value)}
            >
              <option value="20">20 min</option>
              <option value="30">30 min</option>
              <option value="45">45 min</option>
              <option value="60">60 min</option>
              <option value="">Sin límite</option>
            </select>
          </label>
          <label>
            Presentación
            <select
              value={mission}
              onChange={(event) =>
                setMission(event.target.value as StudyMissionType)
              }
            >
              {Object.entries(MISSION_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button
            className="study-primary full"
            disabled={Boolean(busy)}
            type="submit"
          >
            Iniciar estudio libre
          </button>
        </form>
      ) : null}
    </section>
  );
}
