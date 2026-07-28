"use client";

import type {
  ChatHistoryMessage,
  ChatRequest,
  ChatStreamEvent,
  TeacherRole,
} from "@deutschos/shared";
import type { FormEvent } from "react";
import { useRef, useState } from "react";

import { streamNdjson } from "../lib/api";
import {
  refreshModelAvailability,
  useModelAvailability,
} from "../lib/modelAvailability";
import { TeacherMarkdown } from "./TeacherMarkdown";

type Message = { role: "user" | "teacher"; text: string };

const MAX_HISTORY_MESSAGES = 12;
const MAX_HISTORY_CHARACTERS = 12_000;
const MAX_HISTORY_TURN_CHARACTERS = 10_000;

function boundedHistory(messages: Message[]): ChatHistoryMessage[] {
  const history: ChatHistoryMessage[] = [];
  let remainingCharacters = MAX_HISTORY_CHARACTERS;

  for (
    let index = messages.length - 1;
    index >= 0 && history.length < MAX_HISTORY_MESSAGES;
    index -= 1
  ) {
    const message = messages[index];
    if (!message || remainingCharacters <= 0) {
      break;
    }

    const characterLimit = Math.min(
      remainingCharacters,
      MAX_HISTORY_TURN_CHARACTERS,
    );
    const content =
      message.text.length > characterLimit
        ? message.text.slice(-characterLimit)
        : message.text;
    history.unshift({
      role: message.role === "teacher" ? "assistant" : "user",
      content,
    });
    remainingCharacters -= content.length;
  }

  return history;
}

const TEACHER_ROLES: { role: TeacherRole; label: string }[] = [
  { role: "teacher", label: "Profesor" },
  { role: "deep_teacher", label: "Profesor profundo" },
];

function routingRole(role: TeacherRole): "teacher" | "deep" {
  return role === "deep_teacher" ? "deep" : "teacher";
}

function roleError(
  status: ReturnType<typeof useModelAvailability>,
  role: TeacherRole,
): string {
  if (status.status === "error") {
    return status.error ?? "LM Studio no está disponible.";
  }
  if (status.status !== "ready") return "";
  if (!status.data?.lm_studio_available) {
    return "LM Studio no está disponible.";
  }
  if (
    !status.data.roles.find((item) => item.role === routingRole(role))
      ?.available
  ) {
    return "El profesor seleccionado no está disponible en LM Studio.";
  }
  return "";
}

export function Chat() {
  const roleStatus = useModelAvailability();
  const [role, setRole] = useState<TeacherRole>("teacher");
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [waitingForFirstToken, setWaitingForFirstToken] = useState(false);
  const [retryPayload, setRetryPayload] = useState<ChatRequest | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const roleAvailable = Boolean(
    roleStatus.data?.lm_studio_available &&
      roleStatus.data.roles.find((item) => item.role === routingRole(role))
        ?.available,
  );

  async function runRequest(payload: ChatRequest, appendUserMessage: boolean) {
    const controller = new AbortController();
    abortRef.current = controller;
    setError("");
    setRetryPayload(null);
    if (appendUserMessage) {
      setText("");
      setMessages((current) => [
        ...current,
        { role: "user", text: payload.message },
      ]);
    }
    setBusy(true);
    setWaitingForFirstToken(true);

    let teacherMessageStarted = false;
    let streamCompleted = false;

    try {
      for await (const streamEvent of streamNdjson<ChatStreamEvent>(
        "/api/chat/stream",
        {
          method: "POST",
          body: JSON.stringify(payload),
          signal: controller.signal,
        },
      )) {
        if (streamEvent.type === "token") {
          if (typeof streamEvent.content !== "string" || !streamEvent.content) {
            continue;
          }

          if (!teacherMessageStarted) {
            teacherMessageStarted = true;
            setWaitingForFirstToken(false);
            setMessages((current) => [
              ...current,
              { role: "teacher", text: streamEvent.content },
            ]);
          } else {
            setMessages((current) => {
              const next = [...current];
              const last = next.at(-1);
              if (last?.role === "teacher") {
                next[next.length - 1] = {
                  ...last,
                  text: last.text + streamEvent.content,
                };
              }
              return next;
            });
          }
          continue;
        }

        if (streamEvent.type === "done") {
          if (typeof streamEvent.session_id !== "number") {
            throw new Error("La API no confirmó una sesión válida.");
          }
          setSessionId(streamEvent.session_id);
          streamCompleted = true;
          continue;
        }

        if (streamEvent.type === "error") {
          if (streamEvent.retryable) {
            setRetryPayload(payload);
          }
          throw new Error(
            streamEvent.detail || "Falló la respuesta del modelo local.",
          );
        }

        throw new Error("La API devolvió un evento de streaming desconocido.");
      }

      if (!streamCompleted) {
        throw new Error("La respuesta se interrumpió antes de finalizar.");
      }
      if (!teacherMessageStarted) {
        throw new Error("El modelo local no devolvió contenido.");
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(
          cause instanceof Error
            ? cause.message
            : "Ocurrió un error inesperado.",
        );
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      setBusy(false);
      setWaitingForFirstToken(false);
    }
  }

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const prompt = text.trim();
    if (!prompt || !roleAvailable || busy) {
      return;
    }

    void runRequest(
      {
        request_id: crypto.randomUUID(),
        message: prompt,
        role,
        history: boundedHistory(messages),
        session_id: sessionId,
      },
      true,
    );
  }

  const placeholder =
    roleStatus.status === "loading"
      ? "Comprobando LM Studio…"
      : roleStatus.status === "error"
        ? "La API local debe estar activa para conversar"
        : !roleStatus.data?.lm_studio_available
          ? "LM Studio debe estar activo para conversar"
          : !roleAvailable
            ? "El profesor seleccionado no está disponible"
            : "Schreib etwas…";

  return (
    <section className="chat-shell">
      <div className="chat-head">
        <div>
          <p className="eyebrow">PROFESOR LOCAL</p>
          <h1>Practiquemos.</h1>
        </div>
        <div>
          <span
            aria-hidden="true"
            className={roleAvailable ? "dot online" : "dot"}
          />
          <select
            aria-label="Rol del profesor"
            value={role}
            onChange={(event) => {
              const nextRole = event.target.value as TeacherRole;
              setRole(nextRole);
              setError(roleError(roleStatus, nextRole));
              void refreshModelAvailability(0);
            }}
            disabled={busy || sessionId !== null}
          >
            {TEACHER_ROLES.map((item) => (
              <option key={item.role} value={item.role}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="messages" aria-live="polite">
        {messages.length === 0 && (
          <div className="welcome">
            <span>Hallo!</span>
            <h2>¿Qué quieres practicar hoy?</h2>
            <p>
              Escribe en alemán, español o mezcla ambos. Corregiré con calma y
              te haré una pregunta cada vez.
            </p>
          </div>
        )}
        {messages.map((message, index) => (
          <div className={`bubble ${message.role}`} key={index}>
            {message.role === "teacher" ? (
              <TeacherMarkdown>{message.text}</TeacherMarkdown>
            ) : (
              message.text
            )}
          </div>
        ))}
        {waitingForFirstToken ? (
          <div className="bubble teacher muted">
            El profesor está pensando localmente…
          </div>
        ) : null}
        {error && (
          <div className="error" role="alert">
            {error}
            {retryPayload && !busy ? (
              <button
                type="button"
                onClick={() =>
                  void runRequest(
                    {
                      ...retryPayload,
                      request_id: crypto.randomUUID(),
                    },
                    false,
                  )
                }
              >
                Volver a intentar
              </button>
            ) : null}
          </div>
        )}
      </div>

      <form className="composer" onSubmit={send}>
        <textarea
          aria-label="Mensaje para el profesor"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={placeholder}
          disabled={!roleAvailable || busy}
          maxLength={10_000}
        />
        {busy ? (
          <button
            aria-label="Cancelar"
            type="button"
            onClick={() => abortRef.current?.abort()}
          >
            Cancelar
          </button>
        ) : (
          <button aria-label="Enviar" disabled={!text.trim() || !roleAvailable}>
            →
          </button>
        )}
      </form>
      <p className="privacy">
        El historial de esta conversación solo vive en esta pestaña. La base de
        datos local guarda únicamente metadatos de la sesión.
      </p>
    </section>
  );
}
