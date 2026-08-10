"use client";

import type {
  PedagogicalReadingReadiness,
  PedagogicalReadingRun,
} from "@llc/shared";
import { useCallback, useEffect, useState } from "react";

import {
  controlPedagogicalReadingRun,
  createPedagogicalReadingRun,
  getPedagogicalReadingReadiness,
  getPedagogicalReadingRuns,
} from "../lib/api";

const LABELS: Record<string, string> = {
  planned: "Planned",
  queued: "Queued",
  running: "Running",
  paused: "Paused",
  completed: "Completed",
  completed_with_issues: "Completed with issues",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function PedagogicalReadingPanel() {
  const [readiness, setReadiness] =
    useState<PedagogicalReadingReadiness | null>(null);
  const [runs, setRuns] = useState<PedagogicalReadingRun[]>([]);
  const [automatic, setAutomatic] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const [nextReadiness, nextRuns] = await Promise.all([
      getPedagogicalReadingReadiness(),
      getPedagogicalReadingRuns(),
    ]);
    setReadiness(nextReadiness);
    setRuns(nextRuns.filter((run) => run.source_version_id === 584));
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh().catch((cause: unknown) =>
        setError(
          cause instanceof Error ? cause.message : "AI Reading unavailable",
        ),
      );
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const current = runs[0] ?? null;
  const stages = current?.stages ?? [];
  const completed = stages.filter((stage) =>
    stage.state.startsWith("completed"),
  ).length;
  const failed = stages.filter((stage) => stage.state === "failed").length;

  async function act(
    action:
      | "start"
      | "pause"
      | "resume"
      | "cancel"
      | "retry-failed"
      | "next-pass",
  ) {
    setBusy(true);
    setError("");
    try {
      let run = current;
      if (!run) run = await createPedagogicalReadingRun(automatic);
      await controlPedagogicalReadingRun(run.id, action);
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "AI Reading action failed",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="pedagogical-reading-title"
      className="reading-panel"
    >
      <header>
        <div>
          <p className="eyebrow">Herder Grammar</p>
          <h2 id="pedagogical-reading-title">AI Reading</h2>
        </div>
        <strong>
          {current ? (LABELS[current.state] ?? current.state) : "Not started"}
        </strong>
      </header>
      <p>
        Iterative, evidence-bound reading of Grammar v584. No OCR, activation,
        chunks, or embeddings.
      </p>
      <div className="reading-metrics">
        <span>Pass {current?.pass_number ?? "—"}</span>
        <span>{readiness?.topic_count ?? 0} topics</span>
        <span>{completed} completed</span>
        <span>{failed} failed</span>
        <span>{current?.issues ?? 0} issues</span>
        <span>{current?.unresolved_count ?? 0} unresolved</span>
        <span>{current?.conflict_count ?? 0} conflicts</span>
        <span>{current?.resolved_model ?? "Model not resolved"}</span>
      </div>
      <label>
        <input
          checked={automatic}
          disabled={Boolean(current)}
          onChange={(event) => setAutomatic(event.target.checked)}
          type="checkbox"
        />
        Automatic iterative continuation (off by default)
      </label>
      <div className="reading-actions">
        {!current || ["planned", "queued"].includes(current.state) ? (
          <button
            disabled={busy || readiness?.ready === false}
            onClick={() => void act("start")}
            type="button"
          >
            Start
          </button>
        ) : null}
        {current?.state === "running" ? (
          <button
            disabled={busy}
            onClick={() => void act("pause")}
            type="button"
          >
            Pause
          </button>
        ) : null}
        {current?.state === "paused" ? (
          <button
            disabled={busy}
            onClick={() => void act("resume")}
            type="button"
          >
            Resume
          </button>
        ) : null}
        {current &&
        !["completed", "completed_with_issues", "cancelled"].includes(
          current.state,
        ) ? (
          <button
            disabled={busy}
            onClick={() => void act("cancel")}
            type="button"
          >
            Cancel
          </button>
        ) : null}
        {failed ? (
          <button
            disabled={busy}
            onClick={() => void act("retry-failed")}
            type="button"
          >
            Retry failed
          </button>
        ) : null}
        {current?.state.startsWith("completed") ? (
          <button
            disabled={busy}
            onClick={() => void act("next-pass")}
            type="button"
          >
            Prepare next pass
          </button>
        ) : null}
      </div>
      {error ? (
        <p className="laboratory-feedback error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
