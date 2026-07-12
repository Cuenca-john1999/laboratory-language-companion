"use client";

import type {
  ChatHistoryMessage,
  ChatRequest,
  ChatStreamEvent,
  ModelsResponse,
} from "@deutschos/shared";
import type { FormEvent } from "react";
import { useEffect, useState } from "react";

import { getModels, streamNdjson } from "../lib/api";

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

function modelError(models: ModelsResponse): string {
  if (!models.available) {
    return models.error ?? "Ollama no está disponible.";
  }
  if (models.models.length === 0) {
    return "Ollama está activo, pero no hay modelos instalados.";
  }
  return "";
}

export function Chat() {
  const [models, setModels] = useState<ModelsResponse | null>();
  const [model, setModel] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [waitingForFirstToken, setWaitingForFirstToken] = useState(false);

  const hasModels = Boolean(models?.available && models.models.length > 0);

  useEffect(() => {
    getModels()
      .then((value) => {
        setModels(value);
        setModel(value.models[0]?.name ?? "");
        setError(modelError(value));
      })
      .catch((cause: unknown) => {
        setModels(null);
        setError(
          cause instanceof Error
            ? cause.message
            : "No se pudo comprobar el estado de Ollama.",
        );
      });
  }, []);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const prompt = text.trim();
    if (!prompt || !model || busy) {
      return;
    }

    const payload: ChatRequest = {
      message: prompt,
      model,
      history: boundedHistory(messages),
      session_id: sessionId,
    };

    setText("");
    setError("");
    setMessages((current) => [...current, { role: "user", text: prompt }]);
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
      setError(
        cause instanceof Error ? cause.message : "Ocurrió un error inesperado.",
      );
    } finally {
      setBusy(false);
      setWaitingForFirstToken(false);
    }
  }

  const placeholder =
    models === undefined
      ? "Comprobando Ollama…"
      : models === null
        ? "La API local debe estar activa para conversar"
        : !models.available
          ? "Ollama debe estar activo para conversar"
          : models.models.length === 0
            ? "Instala un modelo local para conversar"
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
            className={models?.available ? "dot online" : "dot"}
          />
          <select
            aria-label="Modelo"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            disabled={!hasModels || busy || sessionId !== null}
          >
            {models?.models.length ? (
              models.models.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))
            ) : (
              <option value="">Sin modelos disponibles</option>
            )}
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
            {message.text}
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
          </div>
        )}
      </div>

      <form className="composer" onSubmit={send}>
        <textarea
          aria-label="Mensaje para el profesor"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={placeholder}
          disabled={!hasModels || busy}
          maxLength={10_000}
        />
        <button aria-label="Enviar" disabled={!text.trim() || !model || busy}>
          →
        </button>
      </form>
      <p className="privacy">
        El historial de esta conversación solo vive en esta pestaña. La base de
        datos local guarda únicamente metadatos de la sesión.
      </p>
    </section>
  );
}
