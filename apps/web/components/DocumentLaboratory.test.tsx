import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  detectDocumentChanges,
  getLaboratorySource,
  getLaboratorySources,
  getLaboratorySummary,
} from "../lib/api";
import { DocumentLaboratory } from "./DocumentLaboratory";

vi.mock("../lib/api", () => ({
  cancelDocumentRun: vi.fn(),
  cancelVersionComparison: vi.fn(),
  analyzeStructuredLayout: vi.fn(),
  compareStructuredVersion: vi.fn(),
  consolidateDocumentReview: vi.fn(),
  createDocumentRun: vi.fn(),
  createDocumentRunPass: vi.fn(),
  createStructuredExtractionRun: vi.fn(),
  detectDocumentChanges: vi.fn(),
  decideDocumentReviewItem: vi.fn(),
  executeDocumentPreflight: vi.fn(),
  executeStructuredPreflight: vi.fn(),
  executeVersionComparison: vi.fn(),
  extractStructureCandidates: vi.fn(),
  extractStructuredText: vi.fn(),
  getDocumentRun: vi.fn(),
  getDocumentRunPages: vi.fn(),
  getDocumentRuns: vi.fn().mockResolvedValue([]),
  getDocumentReviewBatches: vi.fn().mockResolvedValue([]),
  getDocumentReviewItem: vi.fn(),
  getDocumentReviewQueue: vi.fn().mockResolvedValue({
    items: [],
    page: 1,
    page_size: 25,
    total: 0,
    pages: 0,
  }),
  getDocumentReviewSummary: vi.fn().mockResolvedValue({ items: [] }),
  getConsolidatedHierarchy: vi.fn().mockResolvedValue([]),
  getConsolidatedTopics: vi.fn().mockResolvedValue([]),
  getExerciseSolutionRelations: vi.fn().mockResolvedValue([]),
  getProposedHierarchy: vi.fn().mockResolvedValue([]),
  getStructureCandidate: vi.fn(),
  getStructureCandidates: vi.fn().mockResolvedValue({
    items: [],
    page: 1,
    page_size: 25,
    total: 0,
    pages: 0,
  }),
  getStructuredPageBlocks: vi.fn().mockResolvedValue([]),
  getPageCorrespondences: vi.fn(),
  getLaboratorySource: vi.fn(),
  getLaboratorySources: vi.fn(),
  getLaboratorySummary: vi.fn(),
  getVersionComparisons: vi.fn().mockResolvedValue([]),
  getDocumentVersions: vi.fn(),
  getComparisonThumbnailUrl: vi.fn(),
  getVersionComparisonEvents: vi.fn(),
  createVersionComparison: vi.fn(),
  applyDocumentAuditDecisions: vi.fn(),
  createDocumentAuditExport: vi.fn(),
  createDocumentClosureSnapshot: vi.fn(),
  dryRunDocumentAuditDecisions: vi.fn(),
  getDocumentClosureSnapshots: vi.fn().mockResolvedValue([]),
  getDocumentClosureStatus: vi.fn(),
  previewDocumentAuditExport: vi.fn(),
  validateDocumentAuditDecisions: vi.fn(),
  confirmPageCorrespondence: vi.fn(),
  rejectPageCorrespondence: vi.fn(),
  revertVersionComparisonEvent: vi.fn(),
  adjustPageCorrespondence: vi.fn(),
  markPageWithoutEquivalent: vi.fn(),
  materializeStructuredPages: vi.fn(),
  pauseDocumentRun: vi.fn(),
  reconcileDocumentCoverage: vi.fn(),
  reconcileStructuredCoverage: vi.fn(),
  repeatStructuredPages: vi.fn(),
  applyDocumentReviewBatch: vi.fn(),
  revertDocumentReviewBatch: vi.fn(),
  resumeDocumentRun: vi.fn(),
  retryDocumentRunStage: vi.fn(),
  structuredThumbnailUrl: vi.fn().mockReturnValue("/thumbnail.png"),
}));

const source = {
  id: "source-1",
  title: "Herder · Gramática",
  collection: null,
  format: ".pdf",
  current_path: "herder.pdf",
  source_status: "present",
  document_state: "candidate" as const,
  needs_manual_review: false,
  active_version_id: 10,
  active_version_number: 1,
  latest_version_id: 11,
  latest_version_number: 2,
  page_count: 200,
  active_chunks: 550,
  active_embeddings: 515,
  needs_ocr: false,
  error_code: null,
  change_pending: true,
};

beforeEach(() => {
  vi.mocked(getLaboratorySummary).mockResolvedValue({
    catalogued_sources: 581,
    recoverable_sources: 44,
    sources_without_content: 537,
    candidate_versions: 2,
    needs_ocr_sources: 5,
    error_sources: 1,
    missing_files: 0,
    pending_jobs: 0,
  });
  vi.mocked(getLaboratorySources).mockResolvedValue([source]);
  vi.mocked(getLaboratorySource).mockResolvedValue({
    ...source,
    canonical_title: source.title,
    display_alias: source.title,
    author: null,
    publisher: null,
    first_seen_at: "2026-07-14T00:00:00Z",
    last_seen_at: "2026-07-30T00:00:00Z",
    versions: [
      {
        id: 11,
        source_id: source.id,
        version_number: 2,
        content_hash: "a".repeat(64),
        size_bytes: 1000,
        mtime_ns: 1,
        observed_path: "herder.pdf",
        observed_name: "herder.pdf",
        detected_at: "2026-07-30T00:00:00Z",
        page_count: 200,
        document_state: "candidate",
        availability_state: "present",
        extraction_state: "pending",
        chunk_state: "pending",
        embedding_state: "pending",
        activation_state: "candidate",
        is_active: false,
        extractor: null,
        extractor_version: null,
        extraction_tool: null,
        extraction_tool_version: null,
        ocr_tool: null,
        ocr_tool_version: null,
        ocr_languages: [],
        technical_metadata: {},
        provenance: "inventory",
        change_reason: "content_hash_changed_at_same_path",
        previous_version_id: 10,
        error_code: null,
        error_detail: null,
        statistics: {},
        processed_at: null,
        chunks: 0,
        embeddings: 0,
      },
    ],
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("document laboratory", () => {
  it("shows separate metrics, filters and active versus detected versions", async () => {
    render(<DocumentLaboratory />);

    expect(
      await screen.findByRole("heading", { name: "Laboratorio documental" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("581")).toBeInTheDocument();
    expect(screen.getByText("Fuentes catalogadas")).toBeInTheDocument();
    expect(screen.getByText("Fuentes recuperables")).toBeInTheDocument();
    expect(screen.getAllByText("Herder · Gramática").length).toBeGreaterThan(0);
    expect(screen.getByText("Activa: v1")).toBeInTheDocument();
    expect(screen.getByText("Detectada: v2")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Extracción" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Revisión" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Matriz 1–51" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Cola" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Batches" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Confirmar todo" }),
    ).not.toBeInTheDocument();
    for (const action of [
      "Ejecutar OCR",
      "Analizar con IA",
      "Crear chunks",
      "Crear embeddings",
    ]) {
      expect(
        screen.queryByRole("button", { name: action }),
      ).not.toBeInTheDocument();
    }

    fireEvent.click(screen.getByRole("button", { name: "Candidatas" }));
    await waitFor(() =>
      expect(getLaboratorySources).toHaveBeenLastCalledWith("candidates"),
    );
  });

  it("requires confirmation and exposes safe inventory progress", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(detectDocumentChanges).mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(
            () =>
              resolve({
                job_id: "inventory-1",
                generated_at: "2026-07-30T00:00:00Z",
                files_scanned: 581,
                unchanged: 580,
                modified: 1,
                new: 0,
                renamed: 0,
                missing: 0,
                duplicates: 0,
                manual_review: 0,
                candidate_versions_created: 1,
                changes: [
                  {
                    outcome: "modified",
                    source_id: source.id,
                    relative_path: source.current_path,
                    title: source.title,
                    previous_hash: "old",
                    current_hash: "new",
                    active_version_id: 10,
                    candidate_version_id: 11,
                    message:
                      "Nueva versión candidata; la versión activa no cambió.",
                  },
                ],
              }),
            1,
          );
        }),
    );

    render(<DocumentLaboratory />);
    const button = await screen.findByRole("button", {
      name: "Detectar cambios",
    });
    fireEvent.click(button);

    expect(confirm).toHaveBeenCalledOnce();
    expect(
      screen.getByRole("button", { name: "Calculando hashes…" }),
    ).toBeDisabled();
    expect(
      await screen.findByText("Inventario completado"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Nueva versión candidata; la versión activa no cambió."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Ejecutar OCR|Analizar con IA/i }),
    ).toBeNull();
  });

  it("opens history and explains source, detected version and active version", async () => {
    render(<DocumentLaboratory />);
    fireEvent.click(await screen.findByRole("button", { name: /Herder/ }));

    expect(
      await screen.findByText(
        "Nueva versión detectada ≠ nueva fuente ≠ versión activa",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Activa para recuperación: v1"),
    ).toBeInTheDocument();
    expect(screen.getByText("Última detectada: v2")).toBeInTheDocument();
    expect(screen.getByText("Versión 2")).toBeInTheDocument();
  });
});
