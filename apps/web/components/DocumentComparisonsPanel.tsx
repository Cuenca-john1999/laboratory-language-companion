"use client";

import type {
  ComparisonEvent,
  DocumentVersion,
  LaboratorySource,
  PageCorrespondence,
  VersionComparison,
} from "@deutschos/shared";
import Image from "next/image";
import { useCallback, useEffect, useState } from "react";

import {
  adjustPageCorrespondence,
  cancelVersionComparison,
  confirmPageCorrespondence,
  createVersionComparison,
  executeVersionComparison,
  getComparisonThumbnailUrl,
  getDocumentVersions,
  getPageCorrespondences,
  getVersionComparisons,
  getVersionComparisonEvents,
  markPageWithoutEquivalent,
  rejectPageCorrespondence,
  revertVersionComparisonEvent,
} from "../lib/api";

function pages(values: number[]): string {
  return values.length ? values.join("–") : "sin equivalente";
}

function numberMetric(summary: Record<string, unknown>, name: string): number {
  return typeof summary[name] === "number" ? summary[name] : 0;
}

export function DocumentComparisonsPanel({
  sources,
}: {
  sources: LaboratorySource[];
}) {
  const [comparisons, setComparisons] = useState<VersionComparison[]>([]);
  const [versions, setVersions] = useState<DocumentVersion[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [baseId, setBaseId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [algorithm, setAlgorithm] = useState("page-match.v1");
  const [windowSize, setWindowSize] = useState(12);
  const [selected, setSelected] = useState<VersionComparison | null>(null);
  const [correspondences, setCorrespondences] = useState<PageCorrespondence[]>(
    [],
  );
  const [events, setEvents] = useState<ComparisonEvent[]>([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setComparisons(await getVersionComparisons());
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh().catch((cause: unknown) =>
        setError(
          cause instanceof Error
            ? cause.message
            : "No se pudieron cargar las comparaciones.",
        ),
      );
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  async function chooseSource(nextSourceId: string) {
    setSourceId(nextSourceId);
    setBaseId("");
    setTargetId("");
    setError("");
    if (!nextSourceId) {
      setVersions([]);
      return;
    }
    try {
      setVersions(await getDocumentVersions(nextSourceId));
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudieron cargar las versiones exactas.",
      );
    }
  }

  async function createComparison() {
    if (!baseId || !targetId || baseId === targetId) return;
    setBusy(true);
    setError("");
    try {
      const created = await createVersionComparison(
        Number(baseId),
        Number(targetId),
        algorithm,
        windowSize,
      );
      setSelected(created);
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo crear la comparación.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function openComparison(comparison: VersionComparison, nextPage = 1) {
    setSelected(comparison);
    setPage(nextPage);
    setError("");
    try {
      const [result, history] = await Promise.all([
        getPageCorrespondences(comparison.id, nextPage),
        getVersionComparisonEvents(comparison.id),
      ]);
      setCorrespondences(result.items);
      setTotalPages(result.pages);
      setEvents(history);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo cargar el mapa de páginas.",
      );
    }
  }

  async function execute() {
    if (!selected) return;
    setBusy(true);
    try {
      const result = await executeVersionComparison(selected.id);
      await openComparison(result);
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "La comparación falló.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function review(
    relation: PageCorrespondence,
    action: "confirm" | "reject",
  ) {
    if (!selected) return;
    try {
      const updated =
        action === "confirm"
          ? await confirmPageCorrespondence(selected.id, relation.id)
          : await rejectPageCorrespondence(selected.id, relation.id);
      setCorrespondences((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setEvents(await getVersionComparisonEvents(selected.id));
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "La revisión no se guardó.",
      );
    }
  }

  async function cancel() {
    if (!selected) return;
    try {
      const updated = await cancelVersionComparison(selected.id);
      setSelected(updated);
      setEvents(await getVersionComparisonEvents(selected.id));
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "La comparación no se pudo cancelar.",
      );
    }
  }

  async function revertDecision(event: ComparisonEvent) {
    if (!selected) return;
    try {
      await revertVersionComparisonEvent(selected.id, event.id);
      await openComparison(selected, page);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "La decisión no se pudo revertir.",
      );
    }
  }

  async function adjust(relation: PageCorrespondence) {
    if (!selected) return;
    const relationType = window.prompt(
      "Tipo: one_to_one, one_to_many, many_to_one o many_to_many",
      relation.relation_type,
    );
    if (!relationType) return;
    const parsePages = (value: string | null) =>
      (value ?? "")
        .split(",")
        .map((item) => Number(item.trim()))
        .filter((item) => Number.isInteger(item) && item > 0);
    const basePages = parsePages(
      window.prompt(
        "Páginas base separadas por comas",
        relation.base_pages.join(","),
      ),
    );
    const targetPages = parsePages(
      window.prompt(
        "Páginas objetivo separadas por comas",
        relation.target_pages.join(","),
      ),
    );
    try {
      const updated = await adjustPageCorrespondence(selected.id, relation.id, {
        base_pages: basePages,
        target_pages: targetPages,
        relation_type: relationType,
        note: "Ajuste manual desde Laboratorio",
      });
      setCorrespondences((current) => [
        ...current.filter((item) => item.id !== relation.id),
        updated,
      ]);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "El ajuste no se guardó.",
      );
    }
  }

  async function markWithoutEquivalent(relation: PageCorrespondence) {
    if (!selected) return;
    const side = relation.base_pages.length ? "base" : "target";
    const pageNumber =
      side === "base" ? relation.base_pages[0] : relation.target_pages[0];
    if (!pageNumber) return;
    const reason = side === "base" ? "deleted" : "inserted";
    try {
      const updated = await markPageWithoutEquivalent(selected.id, {
        side,
        page_number: pageNumber,
        reason,
      });
      setCorrespondences((current) => [...current, updated]);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "La decisión no se guardó.",
      );
    }
  }

  return (
    <section
      className="document-comparisons"
      aria-labelledby="comparisons-title"
    >
      <header>
        <div>
          <p className="eyebrow">Versiones fijadas · evidencia local</p>
          <h2 id="comparisons-title">Comparaciones</h2>
          <p>
            Propone correspondencias por texto, imagen y geometría existentes.
            No ejecuta OCR, IA ni transferencias.
          </p>
        </div>
      </header>

      {error ? (
        <p className="laboratory-feedback error" role="alert">
          {error}
        </p>
      ) : null}

      <div className="comparison-create">
        <label>
          Fuente
          <select
            aria-label="Fuente para comparar"
            onChange={(event) => void chooseSource(event.target.value)}
            value={sourceId}
          >
            <option value="">Selecciona una fuente</option>
            {sources.map((source) => (
              <option key={source.id} value={source.id}>
                {source.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Versión base exacta
          <select
            aria-label="Versión base exacta"
            onChange={(event) => setBaseId(event.target.value)}
            value={baseId}
          >
            <option value="">Selecciona</option>
            {versions.map((version) => (
              <option key={version.id} value={version.id}>
                v{version.version_number} · {version.content_hash.slice(0, 10)}{" "}
                · {version.page_count ?? "?"} págs. ·{" "}
                {version.detected_at.slice(0, 10)} · {version.document_state}
              </option>
            ))}
          </select>
        </label>
        <label>
          Versión objetivo exacta
          <select
            aria-label="Versión objetivo exacta"
            onChange={(event) => setTargetId(event.target.value)}
            value={targetId}
          >
            <option value="">Selecciona</option>
            {versions.map((version) => (
              <option key={version.id} value={version.id}>
                v{version.version_number} · {version.content_hash.slice(0, 10)}{" "}
                · {version.page_count ?? "?"} págs. ·{" "}
                {version.detected_at.slice(0, 10)} · {version.document_state}
              </option>
            ))}
          </select>
        </label>
        <label>
          Algoritmo
          <select
            aria-label="Algoritmo de comparación"
            onChange={(event) => setAlgorithm(event.target.value)}
            value={algorithm}
          >
            <option value="page-match.v1">page-match.v1</option>
          </select>
        </label>
        <label>
          Ventana local
          <input
            aria-label="Ventana local de páginas"
            max={50}
            min={1}
            onChange={(event) => setWindowSize(Number(event.target.value))}
            type="number"
            value={windowSize}
          />
        </label>
        <button
          disabled={busy || !baseId || !targetId || baseId === targetId}
          onClick={() => void createComparison()}
          type="button"
        >
          Crear comparación planificada
        </button>
      </div>

      <div className="comparison-list" aria-label="Lista de comparaciones">
        {comparisons.length ? (
          comparisons.map((comparison) => (
            <button
              key={comparison.id}
              onClick={() => void openComparison(comparison)}
              type="button"
            >
              <span>
                <strong>{comparison.source_title}</strong>
                <small>
                  v{comparison.base_version_number} → v
                  {comparison.target_version_number} ·{" "}
                  {comparison.base_hash.slice(0, 8)} →{" "}
                  {comparison.target_hash.slice(0, 8)}
                </small>
              </span>
              <span className={`run-state ${comparison.state}`}>
                {comparison.state}
              </span>
              <small>
                {numberMetric(comparison.summary, "pending")} pendientes
              </small>
            </button>
          ))
        ) : (
          <p className="laboratory-empty">Todavía no hay comparaciones.</p>
        )}
      </div>

      {selected ? (
        <div
          aria-label={`Comparación de ${selected.source_title}`}
          aria-modal="true"
          className="laboratory-dialog-backdrop"
          role="dialog"
        >
          <section className="laboratory-dialog comparison-dialog">
            <header>
              <div>
                <p className="eyebrow">Mapa paginado · revisión progresiva</p>
                <h2>
                  v{selected.base_version_number} → v
                  {selected.target_version_number}
                </h2>
                <p>
                  {selected.base_hash.slice(0, 12)} →{" "}
                  {selected.target_hash.slice(0, 12)}
                </p>
              </div>
              <button
                aria-label="Cerrar comparación"
                onClick={() => setSelected(null)}
                type="button"
              >
                Cerrar
              </button>
              {selected.state === "planned" || selected.state === "running" ? (
                <button onClick={() => void cancel()} type="button">
                  Cancelar comparación
                </button>
              ) : null}
            </header>

            <div className="comparison-actions">
              <span className={`run-state ${selected.state}`}>
                {selected.state}
              </span>
              <button
                disabled={busy || selected.state === "cancelled"}
                onClick={() => void execute()}
                type="button"
              >
                {selected.state === "planned"
                  ? "Ejecutar comparación"
                  : "Recalcular no confirmadas"}
              </button>
            </div>

            <div
              className="comparison-summary"
              aria-label="Resumen de comparación"
            >
              {[
                ["Base", "base_pages"],
                ["Objetivo", "target_pages"],
                ["1:1", "one_to_one"],
                ["1:N", "one_to_many"],
                ["N:1", "many_to_one"],
                ["Insertadas", "inserted"],
                ["Eliminadas", "deleted"],
                ["Ambiguas", "ambiguous"],
              ].map(([label, key]) => (
                <span key={key}>
                  <strong>{numberMetric(selected.summary, key)}</strong>
                  {label}
                </span>
              ))}
            </div>

            <section className="correspondence-map">
              <header>
                <div>
                  <h3>Mapa de correspondencias</h3>
                  <p>
                    Las miniaturas y el detalle técnico se cargan únicamente al
                    abrir una relación.
                  </p>
                </div>
              </header>
              {correspondences.map((relation) => (
                <details key={relation.id}>
                  <summary>
                    <strong>Base {pages(relation.base_pages)}</strong>
                    <span aria-hidden="true">→</span>
                    <strong>Objetivo {pages(relation.target_pages)}</strong>
                    <span>{relation.relation_type}</span>
                    <span className={`confidence ${relation.confidence}`}>
                      {relation.confidence}
                    </span>
                    <span>{relation.review_state}</span>
                  </summary>
                  <div className="correspondence-detail">
                    <div className="technical-thumbnails">
                      {relation.base_pages.map((pageNumber, position) => (
                        <figure key={`base-${pageNumber}`}>
                          <Image
                            alt={`Miniatura técnica base, página ${pageNumber}`}
                            height={300}
                            loading="lazy"
                            src={getComparisonThumbnailUrl(
                              selected.id,
                              relation.id,
                              "base",
                              position,
                            )}
                            unoptimized
                            width={220}
                          />
                          <figcaption>Base · PDF {pageNumber}</figcaption>
                        </figure>
                      ))}
                      {relation.target_pages.map((pageNumber, position) => (
                        <figure key={`target-${pageNumber}`}>
                          <Image
                            alt={`Miniatura técnica objetivo, página ${pageNumber}`}
                            height={300}
                            loading="lazy"
                            src={getComparisonThumbnailUrl(
                              selected.id,
                              relation.id,
                              "target",
                              position,
                            )}
                            unoptimized
                            width={220}
                          />
                          <figcaption>Objetivo · PDF {pageNumber}</figcaption>
                        </figure>
                      ))}
                    </div>
                    <dl>
                      <div>
                        <dt>Texto</dt>
                        <dd>
                          {relation.text_similarity ?? "Sin texto disponible"}
                        </dd>
                      </div>
                      <div>
                        <dt>Visual</dt>
                        <dd>{relation.visual_similarity ?? "No comparable"}</dd>
                      </div>
                      <div>
                        <dt>Geometría</dt>
                        <dd>
                          {relation.geometry_similarity ?? "No disponible"}
                        </dd>
                      </div>
                      <div>
                        <dt>División</dt>
                        <dd>{relation.split_similarity ?? "No aplica"}</dd>
                      </div>
                    </dl>
                    <details>
                      <summary>Diferencias textuales</summary>
                      <pre>
                        {JSON.stringify(relation.text_difference, null, 2)}
                      </pre>
                    </details>
                    <details>
                      <summary>Evidencia técnica e historial</summary>
                      <pre>{JSON.stringify(relation.evidence, null, 2)}</pre>
                    </details>
                    <div className="comparison-actions">
                      <button
                        onClick={() => void review(relation, "confirm")}
                        type="button"
                      >
                        Confirmar
                      </button>
                      <button
                        onClick={() => void review(relation, "reject")}
                        type="button"
                      >
                        Rechazar
                      </button>
                      <button
                        onClick={() => void adjust(relation)}
                        type="button"
                      >
                        Ajustar
                      </button>
                      <button
                        onClick={() => void markWithoutEquivalent(relation)}
                        type="button"
                      >
                        Marcar sin equivalente
                      </button>
                    </div>
                  </div>
                </details>
              ))}
              <nav className="page-pagination" aria-label="Páginas del mapa">
                <button
                  disabled={page <= 1}
                  onClick={() => void openComparison(selected, page - 1)}
                  type="button"
                >
                  Anterior
                </button>
                <span>
                  Página {page} de {totalPages || 1}
                </span>
                <button
                  disabled={page >= totalPages}
                  onClick={() => void openComparison(selected, page + 1)}
                  type="button"
                >
                  Siguiente
                </button>
              </nav>
            </section>
            <details className="comparison-history">
              <summary>Historial de decisiones</summary>
              <ol>
                {events.map((event) => (
                  <li key={event.id}>
                    <strong>{event.event_type}</strong>
                    <span>
                      {event.previous_state ?? "—"} → {event.new_state ?? "—"}
                    </span>
                    <time dateTime={event.created_at}>
                      {new Date(event.created_at).toLocaleString("es-ES")}
                    </time>
                    {["confirm", "reject", "manual_adjustment"].includes(
                      event.event_type,
                    ) ? (
                      <button
                        onClick={() => void revertDecision(event)}
                        type="button"
                      >
                        Revertir
                      </button>
                    ) : null}
                  </li>
                ))}
              </ol>
            </details>
          </section>
        </div>
      ) : null}
    </section>
  );
}
