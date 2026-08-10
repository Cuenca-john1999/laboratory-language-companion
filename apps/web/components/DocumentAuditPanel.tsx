"use client";

import type {
  AuditDecisionSet,
  AuditExportMode,
  AuditExportPreview,
  AuditValidation,
  ClosureSnapshot,
  ClosureStatus,
  DocumentReviewSummaryItem,
} from "@llc/shared";
import { useCallback, useEffect, useState } from "react";

import {
  applyDocumentAuditDecisions,
  createDocumentAuditExport,
  createDocumentClosureSnapshot,
  dryRunDocumentAuditDecisions,
  getDocumentClosureSnapshots,
  getDocumentClosureStatus,
  getDocumentReviewSummary,
  previewDocumentAuditExport,
  validateDocumentAuditDecisions,
} from "../lib/api";

const MODE_LABELS: Record<AuditExportMode, string> = {
  full: "Auditoría completa",
  review_only: "Solo pendientes",
  targeted: "Selección específica",
};

const AI_LABELS: Record<string, string> = {
  not_ready_for_ai: "No lista para IA",
  ready_for_ai_with_issues: "Lista para IA con pendientes",
  ready_for_ai: "Lista para IA",
  blocked_for_ai: "Bloqueada para IA",
};

type PreviewState = {
  versionId: number;
  request: {
    mode: AuditExportMode;
    selection: Record<string, unknown>;
    include_visuals: boolean;
  };
  result: AuditExportPreview;
};

function byteLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function stateLabel(value: string): string {
  return value.replaceAll("_", " ");
}

export function DocumentAuditPanel() {
  const [items, setItems] = useState<DocumentReviewSummaryItem[]>([]);
  const [statuses, setStatuses] = useState<Record<number, ClosureStatus>>({});
  const [snapshots, setSnapshots] = useState<ClosureSnapshot[]>([]);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [decisionSet, setDecisionSet] = useState<AuditDecisionSet | null>(null);
  const [validation, setValidation] = useState<AuditValidation | null>(null);
  const [dryRun, setDryRun] = useState<AuditValidation | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const summary = await getDocumentReviewSummary();
    const nextStatuses = await Promise.all(
      summary.items.map((item) =>
        getDocumentClosureStatus(item.source_version_id),
      ),
    );
    setItems(summary.items);
    setStatuses(
      Object.fromEntries(
        nextStatuses.map((status) => [status.source_version_id, status]),
      ),
    );
    setSnapshots(await getDocumentClosureSnapshots());
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      refresh().catch((cause: unknown) =>
        setError(
          cause instanceof Error
            ? cause.message
            : "No se pudo cargar Auditoría.",
        ),
      );
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  async function requestPreview(
    versionId: number,
    mode: AuditExportMode,
    includeVisuals = false,
  ) {
    setBusy(true);
    setError("");
    setMessage("");
    const request = {
      mode,
      selection: mode === "targeted" ? { theme_number: 42 } : {},
      include_visuals: includeVisuals,
    };
    try {
      const result = await previewDocumentAuditExport(versionId, request);
      setPreview({ versionId, request, result });
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo preparar el preview.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function createExport() {
    if (!preview) return;
    setBusy(true);
    setError("");
    try {
      const result = await createDocumentAuditExport(
        preview.versionId,
        preview.request,
      );
      setMessage(`Paquete ${result.export_mode} creado y verificado.`);
      setPreview(null);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "No se pudo crear el paquete.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function readDecisionFile(file: File | undefined) {
    setDecisionSet(null);
    setValidation(null);
    setDryRun(null);
    setMessage("");
    setError("");
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as AuditDecisionSet;
      setDecisionSet(parsed);
      setMessage(`${file.name} cargado localmente; aún no se ha aplicado.`);
    } catch {
      setError("El archivo no contiene JSON válido.");
    }
  }

  async function validateImport() {
    if (!decisionSet) return;
    setBusy(true);
    setError("");
    try {
      setValidation(await validateDocumentAuditDecisions(decisionSet));
      setDryRun(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "La validación falló.");
    } finally {
      setBusy(false);
    }
  }

  async function runDryImport() {
    if (!decisionSet || !validation?.valid) return;
    setBusy(true);
    setError("");
    try {
      setDryRun(await dryRunDocumentAuditDecisions(decisionSet));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "El dry-run falló.");
    } finally {
      setBusy(false);
    }
  }

  async function applyImport() {
    if (!decisionSet || !dryRun?.valid) return;
    const confirmed = window.confirm(
      `Se aplicarán ${dryRun.applicable.length} decisiones de forma atómica. ¿Continuar?`,
    );
    if (!confirmed) return;
    setBusy(true);
    setError("");
    try {
      const result = await applyDocumentAuditDecisions(decisionSet);
      setMessage(
        `${result.applied_count} decisiones aplicadas; readiness recalculado.`,
      );
      setDecisionSet(null);
      setValidation(null);
      setDryRun(null);
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "El import atómico falló.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function createSnapshot(versionId: number) {
    setBusy(true);
    setError("");
    try {
      const result = await createDocumentClosureSnapshot(versionId);
      setMessage(
        `Snapshot inmutable ${result.snapshot_hash.slice(0, 12)}… creado.`,
      );
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo crear el snapshot.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="audit-panel" aria-labelledby="audit-title">
      <header>
        <div>
          <p className="eyebrow">Cierre documental</p>
          <h2 id="audit-title">Auditoría</h2>
          <p>
            Exporta evidencia, valida decisiones externas y fija snapshots sin
            activar versiones.
          </p>
        </div>
      </header>

      {error ? (
        <p className="laboratory-feedback error" role="alert">
          {error}
        </p>
      ) : null}
      {message ? (
        <p className="audit-feedback" role="status">
          {message}
        </p>
      ) : null}

      <div className="audit-status-grid">
        {items.map((item) => {
          const status = statuses[item.source_version_id];
          if (!status) return null;
          return (
            <article key={item.source_version_id}>
              <header>
                <div>
                  <h3>{item.source_title}</h3>
                  <small>Versión candidata {item.version_number}</small>
                </div>
                <span className={`audit-readiness ${status.ai_readiness}`}>
                  {AI_LABELS[status.ai_readiness] ??
                    stateLabel(status.ai_readiness)}
                </span>
              </header>
              <dl>
                <div>
                  <dt>Estructura</dt>
                  <dd>{stateLabel(status.structural_readiness)}</dd>
                </div>
                <div>
                  <dt>Bloqueadores</dt>
                  <dd>{status.blockers.length}</dd>
                </div>
                <div>
                  <dt>P1 / P2</dt>
                  <dd>
                    {status.queue.P1} / {status.queue.P2}
                  </dd>
                </div>
                <div>
                  <dt>Temas sin resolver</dt>
                  <dd>{status.unresolved_topics}</dd>
                </div>
                <div>
                  <dt>Visuales</dt>
                  <dd>{status.visual_pending}</dd>
                </div>
                <div>
                  <dt>Último snapshot</dt>
                  <dd>{status.latest_snapshot ? "Disponible" : "—"}</dd>
                </div>
              </dl>
              <div
                className="audit-actions"
                aria-label={`Exportar ${item.source_title}`}
              >
                {(Object.keys(MODE_LABELS) as AuditExportMode[]).map((mode) => (
                  <button
                    disabled={busy}
                    key={mode}
                    onClick={() =>
                      requestPreview(
                        item.source_version_id,
                        mode,
                        mode === "targeted",
                      )
                    }
                    type="button"
                  >
                    Preview · {MODE_LABELS[mode]}
                  </button>
                ))}
                <button
                  disabled={busy}
                  onClick={() => createSnapshot(item.source_version_id)}
                  type="button"
                >
                  Crear snapshot
                </button>
              </div>
            </article>
          );
        })}
      </div>

      {preview ? (
        <aside className="audit-preview" aria-label="Preview de exportación">
          <div>
            <strong>{MODE_LABELS[preview.request.mode]}</strong>
            <span>
              {preview.result.affected_pages.length} páginas ·{" "}
              {Object.values(preview.result.counts).reduce(
                (sum, count) => sum + count,
                0,
              )}{" "}
              elementos · {byteLabel(preview.result.estimated_size_bytes)}{" "}
              estimados · {preview.result.visual_asset_count} imágenes
            </span>
          </div>
          <button disabled={busy} onClick={createExport} type="button">
            Crear paquete
          </button>
          <button
            disabled={busy}
            onClick={() => setPreview(null)}
            type="button"
          >
            Cancelar
          </button>
        </aside>
      ) : null}

      <div className="audit-workflow-grid">
        <section>
          <h3>Importar decisiones</h3>
          <p>
            El archivo se procesa por pasos. Seleccionarlo nunca aplica cambios.
          </p>
          <label className="audit-file-label">
            Archivo de decisiones (.json)
            <input
              accept="application/json,.json"
              onChange={(event) =>
                void readDecisionFile(event.target.files?.[0])
              }
              type="file"
            />
          </label>
          <div className="audit-actions">
            <button
              disabled={busy || !decisionSet}
              onClick={validateImport}
              type="button"
            >
              1. Validar
            </button>
            <button
              disabled={busy || !validation?.valid}
              onClick={runDryImport}
              type="button"
            >
              2. Dry-run
            </button>
            <button
              disabled={
                busy || !dryRun?.valid || dryRun.applicable.length === 0
              }
              onClick={applyImport}
              type="button"
            >
              3. Confirmar y aplicar
            </button>
          </div>
          {validation ? (
            <p role="status">
              Validación: {validation.valid ? "válida" : "rechazada"} ·{" "}
              {validation.stale.length} stale · {validation.invalid.length}{" "}
              inválidas.
            </p>
          ) : null}
          {dryRun ? (
            <p role="status">
              Dry-run: {dryRun.applicable.length} aplicables ·{" "}
              {dryRun.conflicts.length} conflictos · {dryRun.no_effect.length}{" "}
              sin efecto.
            </p>
          ) : null}
        </section>
        <section>
          <h3>Snapshots</h3>
          {snapshots.length ? (
            <ol className="audit-snapshots">
              {snapshots.slice(0, 6).map((snapshot) => (
                <li key={snapshot.id}>
                  <strong>{snapshot.state}</strong>
                  <span>
                    v{snapshot.source_version_id} ·{" "}
                    {snapshot.snapshot_hash.slice(0, 12)}…
                  </span>
                </li>
              ))}
            </ol>
          ) : (
            <p>Aún no hay snapshots de cierre.</p>
          )}
        </section>
      </div>

      <details>
        <summary>Garantías técnicas</summary>
        <p>
          JSON canónico con hashes de integridad; imports ligados a fuente,
          versión, PDF y estado previo. No ejecuta OCR, IA, chunks, embeddings
          ni activación.
        </p>
      </details>
    </section>
  );
}
