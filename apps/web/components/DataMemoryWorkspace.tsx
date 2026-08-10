"use client";

import type { StudyData, StudySessionSummary } from "@llc/shared";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  clearStudySessions,
  deleteStudySession,
  deleteStudySessions,
  getStudyData,
} from "../lib/api";

const STATUS_LABELS: Record<string, string> = {
  planned: "Planificada",
  active: "Activa",
  paused: "Pausada",
  completed: "Completada",
  abandoned: "Abandonada",
};

const PREFERENCE_LABELS: Record<string, string> = {
  automatic: "Automática",
  standard: "Estudio directo",
  laboratory: "Laboratorio",
  frozen_city: "Ciudad helada",
  underwater_exploration: "Expedición submarina",
  space_mission: "Operación espacial",
  mixed: "Sorpresa temática",
};

type Confirmation =
  | { kind: "single"; sessions: StudySessionSummary[] }
  | { kind: "selection"; sessions: StudySessionSummary[] }
  | { kind: "all"; sessions: StudySessionSummary[] };

function dateLabel(value: string): string {
  return new Intl.DateTimeFormat("es-ES", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function errorMessage(cause: unknown): string {
  return cause instanceof Error
    ? cause.message
    : "No se pudo completar la operación.";
}

export function DataMemoryWorkspace() {
  const [data, setData] = useState<StudyData | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [clearText, setClearText] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const confirmButton = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await getStudyData();
      setData(next);
      setSelected((current) =>
        current.filter((id) => next.sessions.some((item) => item.id === id)),
      );
      setError("");
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    if (!confirmation) return;
    confirmButton.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        setConfirmation(null);
        setClearText("");
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [busy, confirmation]);

  const closeConfirmation = () => {
    if (busy) return;
    setConfirmation(null);
    setClearText("");
  };

  const confirmDeletion = async () => {
    if (!confirmation || busy) return;
    if (confirmation.kind === "all" && clearText !== "VACIAR HISTORIAL") {
      return;
    }
    setBusy(true);
    setError("");
    setResult("");
    try {
      const response =
        confirmation.kind === "all"
          ? await clearStudySessions()
          : confirmation.kind === "single"
            ? await deleteStudySession(confirmation.sessions[0].id)
            : await deleteStudySessions(
                confirmation.sessions.map((session) => session.id),
              );
      setSelected([]);
      setConfirmation(null);
      setClearText("");
      setResult(
        response.deleted_sessions === 1
          ? "Se eliminó 1 sesión de Study."
          : `Se eliminaron ${response.deleted_sessions} sesiones de Study.`,
      );
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const sessions = data?.sessions ?? [];
  const selectedSessions = sessions.filter((item) =>
    selected.includes(item.id),
  );
  const allSelected =
    sessions.length > 0 && selected.length === sessions.length;

  return (
    <div className="data-memory-shell">
      <header className="data-memory-heading">
        <div>
          <p className="eyebrow">CONTROL LOCAL</p>
          <h1>Datos y memoria</h1>
          <p>
            Consulta las jornadas guardadas y el estado estructurado que LLC
            conserva sobre tu aprendizaje.
          </p>
        </div>
        <span className="local-data-badge">Solo en este Mac</span>
      </header>

      <aside className="data-separation-note">
        <strong>Sesiones y memoria son cosas distintas.</strong>
        <p>
          Las sesiones son conversaciones o jornadas concretas. La memoria de
          aprendizaje conserva tu recorrido y progreso. Borrar sesiones no
          reinicia automáticamente lo aprendido.
        </p>
      </aside>

      {error ? (
        <p className="data-feedback error" role="alert">
          {error}
        </p>
      ) : null}
      {result ? (
        <p className="data-feedback success" role="status">
          {result}
        </p>
      ) : null}

      <section
        className="data-area sessions-area"
        aria-labelledby="sessions-title"
      >
        <header>
          <div>
            <p className="eyebrow">ÁREA 1</p>
            <h2 id="sessions-title">Sesiones de estudio</h2>
            <p>
              Jornadas concretas de Study. Puedes borrarlas sin tocar la ruta,
              el progreso, las habilidades, las evidencias ni tus preferencias.
            </p>
          </div>
          <span
            className="data-count"
            aria-label={`${data?.total_sessions ?? 0} sesiones`}
          >
            {loading ? "…" : (data?.total_sessions ?? 0)}
          </span>
        </header>

        {loading && !data ? (
          <p className="data-loading" role="status">
            Cargando sesiones locales…
          </p>
        ) : sessions.length ? (
          <>
            <div className="session-selection-toolbar">
              <label>
                <input
                  checked={allSelected}
                  onChange={(event) =>
                    setSelected(
                      event.target.checked
                        ? sessions.map((item) => item.id)
                        : [],
                    )
                  }
                  type="checkbox"
                />
                Seleccionar todas
              </label>
              <span aria-live="polite">
                {selected.length}{" "}
                {selected.length === 1 ? "seleccionada" : "seleccionadas"}
              </span>
              <button
                className="danger-button"
                disabled={selected.length === 0 || busy}
                onClick={() => {
                  if (selectedSessions.length === 0) return;
                  setConfirmation({
                    kind: "selection",
                    sessions: selectedSessions,
                  });
                }}
                type="button"
              >
                Eliminar selección
              </button>
            </div>

            <div className="data-session-list">
              {sessions.map((session) => (
                <article key={session.id}>
                  <label className="session-selector">
                    <input
                      aria-label={`Seleccionar ${session.section_title}`}
                      checked={selected.includes(session.id)}
                      onChange={(event) =>
                        setSelected((current) =>
                          event.target.checked
                            ? [...current, session.id]
                            : current.filter((id) => id !== session.id),
                        )
                      }
                      type="checkbox"
                    />
                  </label>
                  <div className="session-data">
                    <small>{dateLabel(session.started_at)}</small>
                    <h3>{session.section_title}</h3>
                    <p>
                      {session.concept_name ?? "Sin concepto registrado"}
                      {session.mission_label
                        ? ` · ${session.mission_label}`
                        : ""}
                    </p>
                    <code title={session.id}>{session.id}</code>
                  </div>
                  <div className="session-metadata">
                    <span className={`session-status ${session.status}`}>
                      {STATUS_LABELS[session.status] ?? session.status}
                    </span>
                    <small>
                      {Math.round(session.active_seconds / 60)} min activos
                    </small>
                  </div>
                  <button
                    className="danger-button subtle"
                    disabled={busy}
                    onClick={() =>
                      setConfirmation({ kind: "single", sessions: [session] })
                    }
                    type="button"
                  >
                    Eliminar
                  </button>
                </article>
              ))}
            </div>

            <div className="clear-sessions-zone">
              <div>
                <strong>Vaciar historial de sesiones</strong>
                <p>
                  Elimina todas las jornadas de Study. La memoria de aprendizaje
                  que aparece abajo se conserva.
                </p>
              </div>
              <button
                className="danger-button"
                disabled={busy}
                onClick={() => setConfirmation({ kind: "all", sessions })}
                type="button"
              >
                Vaciar historial
              </button>
            </div>
          </>
        ) : (
          <div className="data-empty">
            <h3>No hay sesiones de estudio guardadas.</h3>
            <p>
              Study sigue disponible y la memoria de aprendizaje se muestra
              abajo.
            </p>
            <Link href="/study">Ir a Study</Link>
          </div>
        )}
      </section>

      <section className="data-area memory-area" aria-labelledby="memory-title">
        <header>
          <div>
            <p className="eyebrow">ÁREA 2 · SOLO LECTURA</p>
            <h2 id="memory-title">Memoria de aprendizaje</h2>
            <p>
              Estado local y estructurado que LLC ha persistido sobre tu
              recorrido. Este bloque no ofrece un reinicio total.
            </p>
          </div>
          <span className="memory-lock">Se conserva</span>
        </header>

        {data ? (
          <div className="memory-grid">
            <article>
              <strong>{data.memory.route_topics}</strong>
              <span>temas en la ruta Herder</span>
            </article>
            <article>
              <strong>{data.memory.started_topics}</strong>
              <span>temas con progreso registrado</span>
            </article>
            <article>
              <strong>{data.memory.student_skills}</strong>
              <span>habilidades con estado</span>
            </article>
            <article>
              <strong>{data.memory.skill_evidence}</strong>
              <span>evidencias de aprendizaje</span>
            </article>
            <article>
              <strong>{data.memory.saved_notes}</strong>
              <span>notas guardadas</span>
            </article>
            <article>
              <strong>{data.memory.saved_questions}</strong>
              <span>dudas guardadas</span>
            </article>
          </div>
        ) : (
          <p className="data-loading" role="status">
            Cargando memoria local…
          </p>
        )}

        {data ? (
          <dl className="memory-details">
            <div>
              <dt>Sesión activa</dt>
              <dd>{data.memory.active_session_id ? "Sí" : "Ninguna"}</dd>
            </div>
            <div>
              <dt>Preferencia pedagógica persistida</dt>
              <dd>
                {data.memory.preferences_persisted &&
                data.memory.mission_preference
                  ? (PREFERENCE_LABELS[data.memory.mission_preference] ??
                    data.memory.mission_preference)
                  : "Todavía no guardada"}
              </dd>
            </div>
          </dl>
        ) : null}
      </section>

      {confirmation ? (
        <div className="data-dialog-backdrop">
          <section
            aria-describedby="delete-description"
            aria-labelledby="delete-title"
            aria-modal="true"
            className="data-dialog"
            role="dialog"
          >
            <p className="eyebrow">CONFIRMACIÓN DESTRUCTIVA</p>
            <h2 id="delete-title">
              {confirmation.kind === "all"
                ? "Vaciar todo el historial"
                : confirmation.sessions.length === 1
                  ? `Eliminar “${confirmation.sessions[0].section_title}”`
                  : `Eliminar ${confirmation.sessions.length} sesiones`}
            </h2>
            <p id="delete-description">
              {confirmation.kind === "all"
                ? "Se eliminarán todas las sesiones de Study."
                : "Las conversaciones seleccionadas no podrán recuperarse desde la interfaz."}{" "}
              Tu progreso, habilidades, evidencias, preferencias y ruta de
              aprendizaje se conservarán.
            </p>
            {confirmation.kind === "all" ? (
              <label className="clear-confirmation">
                Escribe <strong>VACIAR HISTORIAL</strong> para continuar
                <input
                  autoComplete="off"
                  onChange={(event) => setClearText(event.target.value)}
                  value={clearText}
                />
              </label>
            ) : null}
            <div className="data-dialog-actions">
              <button disabled={busy} onClick={closeConfirmation} type="button">
                Cancelar
              </button>
              <button
                className="danger-button"
                disabled={
                  busy ||
                  (confirmation.kind === "all" &&
                    clearText !== "VACIAR HISTORIAL")
                }
                onClick={() => void confirmDeletion()}
                ref={confirmButton}
                type="button"
              >
                {busy
                  ? "Eliminando…"
                  : confirmation.kind === "all"
                    ? "Vaciar historial"
                    : "Eliminar definitivamente"}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
