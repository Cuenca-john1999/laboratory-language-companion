"use client";

import type {
  ConsolidatedNode,
  ConsolidatedTopic,
  DocumentReviewBatch,
  DocumentReviewDetail,
  DocumentReviewQueue,
  DocumentReviewSummary,
  ExerciseSolutionRelation,
} from "@llc/shared";
import Image from "next/image";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  applyDocumentReviewBatch,
  consolidateDocumentReview,
  decideDocumentReviewItem,
  getConsolidatedHierarchy,
  getConsolidatedTopics,
  getDocumentReviewBatches,
  getDocumentReviewItem,
  getDocumentReviewQueue,
  getDocumentReviewSummary,
  getExerciseSolutionRelations,
  revertDocumentReviewBatch,
  structuredThumbnailUrl,
} from "../lib/api";

const EDITORIAL_ACTIONS = [
  ["support", "Soportar"],
  ["confirm", "Confirmar"],
  ["reject", "Rechazar"],
  ["conflict", "Marcar conflicto"],
] as const;

function metric(metrics: Record<string, unknown>, key: string): number {
  return typeof metrics[key] === "number" ? metrics[key] : 0;
}

function HierarchyBranch({ node }: { node: ConsolidatedNode }) {
  const label = node.raw_text?.slice(0, 90) || node.node_type;
  if (!node.children.length) {
    return (
      <li>
        {label} <small>· PDF {node.pdf_page_number}</small>
      </li>
    );
  }
  return (
    <li>
      <details>
        <summary>
          {label} <small>· {node.children.length} elementos</small>
        </summary>
        <ul>
          {node.children.map((child) => (
            <HierarchyBranch key={child.id} node={child} />
          ))}
        </ul>
      </details>
    </li>
  );
}

export function DocumentReviewPanel() {
  const [summary, setSummary] = useState<DocumentReviewSummary>({ items: [] });
  const [queue, setQueue] = useState<DocumentReviewQueue | null>(null);
  const [detail, setDetail] = useState<DocumentReviewDetail | null>(null);
  const [batches, setBatches] = useState<DocumentReviewBatch[]>([]);
  const [topics, setTopics] = useState<Record<number, ConsolidatedTopic[]>>({});
  const [hierarchies, setHierarchies] = useState<
    Record<number, ConsolidatedNode[]>
  >({});
  const [relations, setRelations] = useState<
    Record<number, ExerciseSolutionRelation[]>
  >({});
  const [versionFilter, setVersionFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [queuePage, setQueuePage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const queueParameters = useCallback(
    (page = 1) => {
      const params = new URLSearchParams({
        page: String(page),
        page_size: "25",
      });
      if (versionFilter) params.set("source_version_id", versionFilter);
      if (priorityFilter) params.set("priority", priorityFilter);
      if (typeFilter) params.set("candidate_type", typeFilter);
      return params;
    },
    [priorityFilter, typeFilter, versionFilter],
  );

  const loadAll = useCallback(async () => {
    const nextSummary = await getDocumentReviewSummary();
    const versionIds = nextSummary.items
      .filter((item) => item.run_id !== null)
      .map((item) => item.source_version_id);
    const [
      nextQueue,
      nextBatches,
      topicValues,
      hierarchyValues,
      relationValues,
    ] = await Promise.all([
      getDocumentReviewQueue(queueParameters(1)),
      getDocumentReviewBatches(),
      Promise.all(versionIds.map((version) => getConsolidatedTopics(version))),
      Promise.all(
        versionIds.map((version) => getConsolidatedHierarchy(version)),
      ),
      Promise.all(
        versionIds.map((version) => getExerciseSolutionRelations(version)),
      ),
    ]);
    setSummary(nextSummary);
    setQueue(nextQueue);
    setBatches(nextBatches);
    setTopics(
      Object.fromEntries(
        versionIds.map((version, index) => [version, topicValues[index]]),
      ),
    );
    setHierarchies(
      Object.fromEntries(
        versionIds.map((version, index) => [version, hierarchyValues[index]]),
      ),
    );
    setRelations(
      Object.fromEntries(
        versionIds.map((version, index) => [version, relationValues[index]]),
      ),
    );
    setQueuePage(1);
  }, [queueParameters]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadAll().catch(() =>
        setError("No se pudo cargar la revisión documental."),
      );
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadAll]);

  async function consolidate(sourceVersionId: number) {
    setBusy(true);
    setError("");
    try {
      await consolidateDocumentReview(sourceVersionId);
      await loadAll();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo consolidar la versión.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function filterQueue() {
    setBusy(true);
    try {
      setQueue(await getDocumentReviewQueue(queueParameters(1)));
      setQueuePage(1);
      setDetail(null);
    } finally {
      setBusy(false);
    }
  }

  async function changeQueuePage(page: number) {
    setQueue(await getDocumentReviewQueue(queueParameters(page)));
    setQueuePage(page);
    setDetail(null);
  }

  async function decide(
    action: "support" | "confirm" | "reject" | "conflict" | "supersede",
    visual = false,
  ) {
    if (!detail) return;
    setBusy(true);
    setError("");
    try {
      await decideDocumentReviewItem(detail.id, action, visual);
      await loadAll();
      setDetail(null);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo registrar la decisión.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function changeBatch(batch: DocumentReviewBatch) {
    setBusy(true);
    try {
      if (batch.state === "proposed") await applyDocumentReviewBatch(batch.id);
      if (batch.state === "applied") await revertDocumentReviewBatch(batch.id);
      await loadAll();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "No se pudo actualizar el lote.",
      );
    } finally {
      setBusy(false);
    }
  }

  const matrix = useMemo(
    () =>
      Array.from({ length: 51 }, (_, index) => {
        const number = index + 1;
        return {
          number,
          cells: summary.items.map((item) =>
            topics[item.source_version_id]?.find(
              (topic) => topic.theme_number === number,
            ),
          ),
        };
      }),
    [summary.items, topics],
  );

  return (
    <section
      className="document-review"
      aria-labelledby="document-review-title"
    >
      <header>
        <div>
          <p className="eyebrow">Evidencia → soporte → revisión</p>
          <h2 id="document-review-title">Revisión</h2>
          <p>
            Consolida estructura sin corregir el OCR, crear conocimiento,
            ejecutar IA ni activar versiones.
          </p>
        </div>
      </header>

      {error ? (
        <p className="laboratory-feedback error" role="alert">
          {error}
        </p>
      ) : null}

      <div
        className="review-document-grid"
        aria-label="Resumen de fiabilidad documental"
      >
        {summary.items.map((item) => (
          <article key={item.source_version_id}>
            <div>
              <small>
                v{item.version_number} · {item.hash.slice(0, 12)}
              </small>
              <h3>{item.source_title}</h3>
            </div>
            <strong className={`readiness ${item.readiness}`}>
              {item.readiness}
            </strong>
            <dl>
              <div>
                <dt>Temas soportados</dt>
                <dd>{metric(item.metrics, "topics_auto_supported")}</dd>
              </div>
              <div>
                <dt>P0</dt>
                <dd>{item.queue.P0}</dd>
              </div>
              <div>
                <dt>P1</dt>
                <dd>{item.queue.P1}</dd>
              </div>
              <div>
                <dt>P2</dt>
                <dd>{item.queue.P2}</dd>
              </div>
              <div>
                <dt>Conflictos</dt>
                <dd>{metric(item.metrics, "conflicted")}</dd>
              </div>
              <div>
                <dt>Rechazados</dt>
                <dd>{metric(item.metrics, "rejected")}</dd>
              </div>
              <div>
                <dt>Superseded</dt>
                <dd>{metric(item.metrics, "superseded")}</dd>
              </div>
              <div>
                <dt>Asociaciones</dt>
                <dd>
                  {
                    (relations[item.source_version_id] ?? []).filter(
                      (relation) =>
                        ["one_to_one", "one_to_many", "many_to_one"].includes(
                          relation.relation_type,
                        ),
                    ).length
                  }
                </dd>
              </div>
              <div>
                <dt>Relaciones pendientes</dt>
                <dd>
                  {
                    (relations[item.source_version_id] ?? []).filter(
                      (relation) => relation.state === "needs_review",
                    ).length
                  }
                </dd>
              </div>
              <div>
                <dt>Visual pendiente</dt>
                <dd>{metric(item.metrics, "visual_pages_pending")}</dd>
              </div>
            </dl>
            {item.blockers.length ? (
              <p className="review-blocker">
                Bloqueadores: {item.blockers.join(", ")}
              </p>
            ) : null}
            <button
              disabled={busy}
              onClick={() => void consolidate(item.source_version_id)}
              type="button"
            >
              {item.run_id
                ? "Recalcular consolidación"
                : "Consolidar estructura"}
            </button>
          </article>
        ))}
      </div>

      <section className="topic-matrix" aria-labelledby="topic-matrix-title">
        <header>
          <div>
            <p className="eyebrow">Identidad principal</p>
            <h3 id="topic-matrix-title">Matriz 1–51</h3>
          </div>
          <span>
            Una instancia principal por libro; los alternativos conservan
            trazabilidad.
          </span>
        </header>
        <div className="topic-matrix-scroll">
          <table>
            <thead>
              <tr>
                <th>Tema</th>
                {summary.items.map((item) => (
                  <th key={item.source_version_id}>{item.source_title}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.map((row) => (
                <tr key={row.number}>
                  <th>{row.number}</th>
                  {row.cells.map((topic, index) => (
                    <td key={summary.items[index]?.source_version_id ?? index}>
                      <span
                        className={`topic-state ${topic?.state ?? "pending"}`}
                      >
                        {topic?.state ?? "pendiente"}
                      </span>
                      {topic?.pdf_page_number ? (
                        <small>PDF {topic.pdf_page_number}</small>
                      ) : null}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="review-queue" aria-labelledby="review-queue-title">
        <header>
          <div>
            <p className="eyebrow">Priorización determinista</p>
            <h3 id="review-queue-title">Cola</h3>
          </div>
          <div className="review-filters" aria-label="Filtros de revisión">
            <label>
              Fuente
              <select
                value={versionFilter}
                onChange={(event) => setVersionFilter(event.target.value)}
              >
                <option value="">Ambas</option>
                {summary.items.map((item) => (
                  <option
                    key={item.source_version_id}
                    value={item.source_version_id}
                  >
                    {item.source_title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Prioridad
              <select
                value={priorityFilter}
                onChange={(event) => setPriorityFilter(event.target.value)}
              >
                <option value="">Todas</option>
                <option>P0</option>
                <option>P1</option>
                <option>P2</option>
              </select>
            </label>
            <label>
              Tipo
              <input
                value={typeFilter}
                onChange={(event) => setTypeFilter(event.target.value)}
                placeholder="table, topic_heading…"
              />
            </label>
            <button
              disabled={busy}
              onClick={() => void filterQueue()}
              type="button"
            >
              Filtrar
            </button>
          </div>
        </header>
        <div className="review-table-scroll">
          <table>
            <thead>
              <tr>
                <th>Prioridad</th>
                <th>Fuente</th>
                <th>Página</th>
                <th>Tema</th>
                <th>Tipo</th>
                <th>Motivo</th>
                <th>Confianza</th>
              </tr>
            </thead>
            <tbody>
              {queue?.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <span className={`priority ${item.priority.toLowerCase()}`}>
                      {item.priority}
                    </span>
                  </td>
                  <td>v{item.source_version_id}</td>
                  <td>{item.pdf_page_number}</td>
                  <td>{item.canonical_topic_number ?? "—"}</td>
                  <td>{item.candidate_type}</td>
                  <td>
                    <button
                      className="text-button"
                      onClick={() =>
                        void getDocumentReviewItem(item.id).then(setDetail)
                      }
                      type="button"
                    >
                      {item.reason ?? "revisión"}
                    </button>
                  </td>
                  <td>{Math.round(item.effective_confidence * 100)} %</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <footer>
          <button
            disabled={!queue || queuePage <= 1}
            onClick={() => void changeQueuePage(queuePage - 1)}
            type="button"
          >
            Anterior
          </button>
          <span>
            Página {queuePage} de {queue?.pages || 1} · {queue?.total ?? 0}{" "}
            pendientes
          </span>
          <button
            disabled={!queue || queuePage >= queue.pages}
            onClick={() => void changeQueuePage(queuePage + 1)}
            type="button"
          >
            Siguiente
          </button>
        </footer>
      </section>

      {detail ? (
        <section
          className="focused-review"
          aria-labelledby="focused-review-title"
        >
          <header>
            <div>
              <p className="eyebrow">Decisión individual append-only</p>
              <h3 id="focused-review-title">Revisión focalizada</h3>
            </div>
            <button onClick={() => setDetail(null)} type="button">
              Cerrar
            </button>
          </header>
          <div className="focused-review-grid">
            <figure>
              <Image
                alt={`Página PDF ${detail.pdf_page_number}`}
                height={760}
                src={structuredThumbnailUrl(
                  detail.source_version_id,
                  detail.pdf_page_number,
                )}
                unoptimized
                width={540}
              />
              <figcaption>
                PDF {detail.pdf_page_number} · región{" "}
                {detail.bbox?.join(", ") ?? "sin bbox"}
              </figcaption>
            </figure>
            <div>
              <p>
                <strong>
                  {detail.priority} · {detail.candidate_type}
                </strong>{" "}
                · {detail.reason}
              </p>
              <h4>OCR observado</h4>
              <pre>{detail.raw_text || "Sin texto observado"}</pre>
              <h4>Normalizado conservador</h4>
              <pre>{detail.normalized_layout_text || "Sin normalización"}</pre>
              <details>
                <summary>Contexto y evidencia</summary>
                <pre>
                  {JSON.stringify(
                    {
                      parent: detail.parent,
                      neighbors: detail.neighbors,
                      children: detail.children,
                      evidence: detail.evidence,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
              <div
                className="review-actions"
                aria-label="Acciones editoriales individuales"
              >
                {EDITORIAL_ACTIONS.map(([action, label]) => (
                  <button
                    disabled={busy}
                    key={action}
                    onClick={() => void decide(action)}
                    type="button"
                  >
                    {label}
                  </button>
                ))}
                <button
                  disabled={busy}
                  onClick={() => void decide("support", true)}
                  type="button"
                >
                  Soportar tras revisión visual
                </button>
              </div>
              <small>
                No existe acción “confirmar todo”. Cada decisión conserva actor,
                método, evidencia y estado anterior.
              </small>
            </div>
          </div>
        </section>
      ) : null}

      <section
        className="review-batches"
        aria-labelledby="review-batches-title"
      >
        <header>
          <div>
            <p className="eyebrow">Patrones homogéneos</p>
            <h3 id="review-batches-title">Batches</h3>
          </div>
          <span>Ningún lote se aplica automáticamente.</span>
        </header>
        <div>
          {batches.map((batch) => (
            <article key={batch.id}>
              <strong>{batch.rule}</strong>
              <span>
                {batch.member_count} · {batch.candidate_type}
              </span>
              <small>
                {batch.proposed_action} · confianza mínima{" "}
                {Math.round(batch.minimum_confidence * 100)} % · {batch.state}
              </small>
              {batch.state === "proposed" || batch.state === "applied" ? (
                <button
                  disabled={busy}
                  onClick={() => void changeBatch(batch)}
                  type="button"
                >
                  {batch.state === "proposed"
                    ? "Aplicar lote"
                    : "Deshacer lote"}
                </button>
              ) : null}
            </article>
          ))}
        </div>
      </section>

      <section
        className="consolidated-hierarchy"
        aria-labelledby="consolidated-hierarchy-title"
      >
        <header>
          <div>
            <p className="eyebrow">Candidatos originales enlazados</p>
            <h3 id="consolidated-hierarchy-title">Jerarquía consolidada</h3>
          </div>
        </header>
        {summary.items.map((item) => (
          <details key={item.source_version_id}>
            <summary>
              {item.source_title} ·{" "}
              {(hierarchies[item.source_version_id] ?? []).length} temas raíz
            </summary>
            <ul>
              {(hierarchies[item.source_version_id] ?? []).map((node) => (
                <HierarchyBranch key={node.id} node={node} />
              ))}
            </ul>
          </details>
        ))}
      </section>
    </section>
  );
}
