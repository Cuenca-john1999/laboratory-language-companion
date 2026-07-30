"use client";

import type {
  DocumentInventoryResult,
  DocumentState,
  LaboratorySource,
  LaboratorySourceDetail,
  LaboratorySummary,
} from "@deutschos/shared";
import { useCallback, useEffect, useState } from "react";

import {
  detectDocumentChanges,
  getLaboratorySource,
  getLaboratorySources,
  getLaboratorySummary,
} from "../lib/api";
import { DocumentRunsPanel } from "./DocumentRunsPanel";

const FILTERS = [
  ["all", "Todas"],
  ["active", "Activas"],
  ["candidates", "Candidatas"],
  ["needs_ocr", "Necesitan OCR"],
  ["errors", "Con error"],
  ["without_content", "Sin contenido"],
  ["missing", "Ausentes"],
] as const;

const STATE_LABELS: Record<DocumentState, string> = {
  unchanged: "Sin cambios",
  detected: "Detectada",
  candidate: "Candidata",
  pending_extraction: "Extracción pendiente",
  needs_ocr: "Necesita OCR",
  processing: "En procesamiento",
  pending_validation: "Validación pendiente",
  ready: "Lista para activar",
  active: "Activa",
  historical: "Histórica",
  failed: "Fallida",
  missing: "Archivo ausente",
  manual_review: "Revisión manual",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${unit}`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("es-ES", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function VersionMarker({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | null;
  tone: "active" | "candidate";
}) {
  return (
    <span className={`version-marker ${tone}`}>
      {label}: {value === null ? "ninguna" : `v${value}`}
    </span>
  );
}

export function DocumentLaboratory() {
  const [summary, setSummary] = useState<LaboratorySummary | null>(null);
  const [sources, setSources] = useState<LaboratorySource[]>([]);
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<LaboratorySourceDetail | null>(null);
  const [inventory, setInventory] = useState<DocumentInventoryResult | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async (nextFilter: string) => {
    const [nextSummary, nextSources] = await Promise.all([
      getLaboratorySummary(),
      getLaboratorySources(nextFilter),
    ]);
    setSummary(nextSummary);
    setSources(nextSources);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      refresh(filter)
        .catch((cause: unknown) =>
          setError(
            cause instanceof Error
              ? cause.message
              : "No se pudo cargar el laboratorio documental.",
          ),
        )
        .finally(() => setLoading(false));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [filter, refresh]);

  async function selectSource(sourceId: string) {
    setError("");
    try {
      setSelected(await getLaboratorySource(sourceId));
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo cargar el historial documental.",
      );
    }
  }

  async function runInventory() {
    const confirmed = window.confirm(
      "Se revisarán únicamente archivos, tamaños y hashes. No se ejecutarán OCR, IA, chunking ni embeddings. ¿Detectar cambios ahora?",
    );
    if (!confirmed) return;
    setDetecting(true);
    setError("");
    try {
      const result = await detectDocumentChanges();
      setInventory(result);
      await refresh(filter);
      if (selected) {
        setSelected(await getLaboratorySource(selected.id));
      }
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "El inventario falló sin aplicar cambios parciales.",
      );
    } finally {
      setDetecting(false);
    }
  }

  function selectFilter(nextFilter: string) {
    setLoading(true);
    setError("");
    setFilter(nextFilter);
  }

  const metrics = summary
    ? [
        ["Fuentes catalogadas", summary.catalogued_sources],
        ["Fuentes recuperables", summary.recoverable_sources],
        ["Fuentes sin contenido", summary.sources_without_content],
        ["Versiones candidatas", summary.candidate_versions],
        ["Necesitan OCR", summary.needs_ocr_sources],
        ["Fuentes con errores", summary.error_sources],
        ["Archivos ausentes", summary.missing_files],
        ["Trabajos pendientes", summary.pending_jobs],
      ]
    : [];

  return (
    <section className="laboratory-shell">
      <header className="laboratory-heading">
        <div>
          <p className="eyebrow">Biblioteca · control de corpus</p>
          <h1>Laboratorio documental</h1>
          <p>
            Una fuente representa una obra estable. Sus archivos pueden cambiar
            y crear versiones candidatas sin alterar la recuperación activa.
          </p>
        </div>
        <button
          className="primary-button inventory-button"
          disabled={detecting}
          onClick={runInventory}
          type="button"
        >
          {detecting ? "Calculando hashes…" : "Detectar cambios"}
        </button>
      </header>

      <aside className="laboratory-safety-note">
        <strong>Inventario seguro</strong>
        <span>
          Solo observa archivos y hashes. No ejecuta OCR, modelos locales,
          extracción, chunks ni embeddings.
        </span>
      </aside>

      {error ? (
        <p className="laboratory-feedback error" role="alert">
          {error}
        </p>
      ) : null}

      <div aria-label="Resumen documental" className="laboratory-metrics">
        {metrics.map(([label, value]) => (
          <article key={label}>
            <strong>{value}</strong>
            <span>{label}</span>
          </article>
        ))}
      </div>

      <DocumentRunsPanel sources={sources} />

      {inventory ? (
        <section className="inventory-result" aria-live="polite">
          <header>
            <div>
              <p className="eyebrow">Última detección</p>
              <h2>Inventario completado</h2>
            </div>
            <time dateTime={inventory.generated_at}>
              {formatDate(inventory.generated_at)}
            </time>
          </header>
          <p>
            {inventory.files_scanned} archivos · {inventory.modified}{" "}
            modificados · {inventory.new} nuevos · {inventory.renamed}{" "}
            renombrados · {inventory.missing} ausentes
          </p>
          {inventory.changes.some(
            (change) => change.outcome !== "unchanged",
          ) ? (
            <ul>
              {inventory.changes
                .filter((change) => change.outcome !== "unchanged")
                .map((change) => (
                  <li key={`${change.source_id}-${change.relative_path}`}>
                    <strong>{change.title}</strong>
                    <span>{change.message}</span>
                  </li>
                ))}
            </ul>
          ) : (
            <p>No se detectaron cambios documentales.</p>
          )}
        </section>
      ) : null}

      <section className="document-catalogue">
        <header>
          <div>
            <p className="eyebrow">Estado por obra</p>
            <h2>Documentos</h2>
          </div>
          <div aria-label="Filtros documentales" className="laboratory-filters">
            {FILTERS.map(([value, label]) => (
              <button
                aria-pressed={filter === value}
                key={value}
                onClick={() => selectFilter(value)}
                type="button"
              >
                {label}
              </button>
            ))}
          </div>
        </header>

        {loading ? (
          <p className="laboratory-empty">Cargando estado documental…</p>
        ) : sources.length === 0 ? (
          <p className="laboratory-empty">
            No hay fuentes que coincidan con este filtro.
          </p>
        ) : (
          <div className="document-list">
            {sources.map((source) => (
              <button
                className="document-row"
                key={source.id}
                onClick={() => selectSource(source.id)}
                type="button"
              >
                <span className="document-identity">
                  <strong>{source.title}</strong>
                  <small>
                    {source.collection ?? "Corpus principal"} ·{" "}
                    {source.format.replace(".", "").toUpperCase()}
                    {source.page_count ? ` · ${source.page_count} páginas` : ""}
                  </small>
                </span>
                <span className={`document-state ${source.document_state}`}>
                  {STATE_LABELS[source.document_state]}
                </span>
                <span className="document-versions">
                  <VersionMarker
                    label="Activa"
                    tone="active"
                    value={source.active_version_number}
                  />
                  <VersionMarker
                    label="Detectada"
                    tone="candidate"
                    value={source.latest_version_number}
                  />
                </span>
                <span className="document-coverage">
                  <small>{source.active_chunks} chunks</small>
                  <small>{source.active_embeddings} embeddings</small>
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      {selected ? (
        <div
          aria-label={`Detalle de ${selected.title}`}
          aria-modal="true"
          className="laboratory-dialog-backdrop"
          role="dialog"
        >
          <section className="laboratory-dialog">
            <header>
              <div>
                <p className="eyebrow">Identidad estable de fuente</p>
                <h2>{selected.title}</h2>
                <p>{selected.current_path}</p>
              </div>
              <button
                aria-label="Cerrar detalle"
                onClick={() => setSelected(null)}
                type="button"
              >
                Cerrar
              </button>
            </header>

            <div className="version-explanation">
              <strong>
                Nueva versión detectada ≠ nueva fuente ≠ versión activa
              </strong>
              <span>
                La recuperación solo consulta la versión marcada como activa.
              </span>
            </div>

            <div className="detail-version-markers">
              <VersionMarker
                label="Activa para recuperación"
                tone="active"
                value={selected.active_version_number}
              />
              <VersionMarker
                label="Última detectada"
                tone="candidate"
                value={selected.latest_version_number}
              />
            </div>

            <div className="version-history">
              {selected.versions.map((version) => (
                <article key={version.id}>
                  <header>
                    <div>
                      <strong>Versión {version.version_number}</strong>
                      <span
                        className={`document-state ${version.document_state}`}
                      >
                        {STATE_LABELS[version.document_state]}
                      </span>
                    </div>
                    <code>{version.content_hash.slice(0, 12)}</code>
                  </header>
                  <dl>
                    <div>
                      <dt>Archivo</dt>
                      <dd>{version.observed_name ?? "Sin nombre observado"}</dd>
                    </div>
                    <div>
                      <dt>Detectada</dt>
                      <dd>{formatDate(version.detected_at)}</dd>
                    </div>
                    <div>
                      <dt>Tamaño</dt>
                      <dd>{formatBytes(version.size_bytes)}</dd>
                    </div>
                    <div>
                      <dt>Páginas</dt>
                      <dd>{version.page_count ?? "Desconocidas"}</dd>
                    </div>
                    <div>
                      <dt>Extracción</dt>
                      <dd>{version.extraction_state}</dd>
                    </div>
                    <div>
                      <dt>Chunks / embeddings</dt>
                      <dd>
                        {version.chunks} / {version.embeddings}
                      </dd>
                    </div>
                    <div>
                      <dt>Procedencia</dt>
                      <dd>{version.provenance}</dd>
                    </div>
                    <div>
                      <dt>Versión anterior</dt>
                      <dd>
                        {version.previous_version_id
                          ? `#${version.previous_version_id}`
                          : "Primera versión"}
                      </dd>
                    </div>
                  </dl>
                </article>
              ))}
            </div>
          </section>
        </div>
      ) : null}
    </section>
  );
}
