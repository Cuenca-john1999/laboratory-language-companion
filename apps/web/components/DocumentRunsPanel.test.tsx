import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  executeDocumentPreflight,
  getDocumentRun,
  getDocumentRunPages,
  getDocumentRuns,
} from "../lib/api";
import { DocumentRunsPanel } from "./DocumentRunsPanel";

vi.mock("../lib/api", () => ({
  cancelDocumentRun: vi.fn(),
  createDocumentRun: vi.fn(),
  createDocumentRunPass: vi.fn(),
  executeDocumentPreflight: vi.fn(),
  getDocumentRun: vi.fn(),
  getDocumentRunPages: vi.fn(),
  getDocumentRuns: vi.fn(),
  pauseDocumentRun: vi.fn(),
  reconcileDocumentCoverage: vi.fn(),
  resumeDocumentRun: vi.fn(),
  retryDocumentRunStage: vi.fn(),
}));

const source = {
  id: "source-1",
  title: "Fixture documental",
  collection: null,
  format: ".pdf",
  current_path: "fixture.pdf",
  source_status: "present",
  document_state: "candidate" as const,
  needs_manual_review: false,
  active_version_id: null,
  active_version_number: null,
  latest_version_id: 4,
  latest_version_number: 2,
  page_count: 428,
  active_chunks: 0,
  active_embeddings: 0,
  needs_ocr: false,
  error_code: null,
  change_pending: true,
};

const run = {
  id: "run-1",
  source_id: source.id,
  source_title: source.title,
  source_version_id: 4,
  source_version_number: 2,
  target_hash: "a".repeat(64),
  run_type: "document_analysis",
  pipeline_version: "document-pipeline.v1",
  configuration: { safe_stages_only: true },
  configuration_hash: "b".repeat(64),
  selection_strategy: "all_pages",
  selected_pages: [],
  reused_results: {},
  state: "planned" as const,
  parent_run_id: null,
  base_run_id: null,
  initiated_by: null,
  reason: "fixture",
  summary: {},
  error_code: null,
  error_detail: null,
  resumable: true,
  exclusive: true,
  created_at: "2026-07-30T12:00:00Z",
  started_at: null,
  completed_at: null,
  updated_at: "2026-07-30T12:00:00Z",
  current_stage: null,
  issue_count: 0,
};

const detail = {
  ...run,
  stages: [
    {
      id: "stage-1",
      run_id: run.id,
      name: "preflight",
      version: "preflight.v1",
      state: "pending" as const,
      attempt: 1,
      configuration: {},
      metrics: {},
      dependencies: [],
      error_code: null,
      error_detail: null,
      resumable: true,
      started_at: null,
      completed_at: null,
      created_at: run.created_at,
      updated_at: run.updated_at,
    },
    {
      id: "stage-2",
      run_id: run.id,
      name: "ocr",
      version: "ocr.v1",
      state: "not_scheduled" as const,
      attempt: 1,
      configuration: {},
      metrics: {},
      dependencies: ["preflight"],
      error_code: null,
      error_detail: null,
      resumable: false,
      started_at: null,
      completed_at: null,
      created_at: run.created_at,
      updated_at: run.updated_at,
    },
  ],
  coverage: [
    {
      id: "coverage-1",
      run_id: run.id,
      source_version_id: 4,
      stage_name: "coverage_reconciliation",
      dimension: "text",
      execution_status: "executed" as const,
      denominator: 428,
      completed: 312,
      with_issues: 3,
      failed: 0,
      pending: 116,
      not_applicable: 0,
      unknown: 0,
      breakdown: {},
      provenance: {},
      captured_at: run.created_at,
    },
    {
      id: "coverage-2",
      run_id: run.id,
      source_version_id: 4,
      stage_name: "coverage_reconciliation",
      dimension: "structure",
      execution_status: "not_executed" as const,
      denominator: 428,
      completed: null,
      with_issues: null,
      failed: null,
      pending: null,
      not_applicable: null,
      unknown: 428,
      breakdown: {},
      provenance: {},
      captured_at: run.created_at,
    },
    {
      id: "coverage-3",
      run_id: run.id,
      source_version_id: 4,
      stage_name: "coverage_reconciliation",
      dimension: "canonical_mapping",
      execution_status: "no_data" as const,
      denominator: null,
      completed: null,
      with_issues: null,
      failed: null,
      pending: null,
      not_applicable: null,
      unknown: null,
      breakdown: {},
      provenance: {},
      captured_at: run.created_at,
    },
  ],
  events: [
    {
      id: "event-1",
      run_id: run.id,
      event_type: "run_created",
      actor: null,
      from_state: null,
      to_state: "planned",
      detail: {},
      created_at: run.created_at,
    },
  ],
};

beforeEach(() => {
  vi.mocked(getDocumentRuns).mockResolvedValue([run]);
  vi.mocked(getDocumentRun).mockResolvedValue(detail);
  vi.mocked(getDocumentRunPages).mockResolvedValue({
    items: [
      {
        id: 1,
        source_version_id: 4,
        pdf_page_number: 1,
        printed_page_number: null,
        fingerprint: null,
        width_points: null,
        height_points: null,
        rotation_degrees: null,
        has_text: true,
        character_count: 120,
        text_quality: "good",
        layout_state: null,
        structure_state: null,
        review_state: "unreviewed",
        issue_count: 0,
        stage_states: {
          structural_extraction: "pending",
          review: "needs_review",
          chunking: "completed",
          embeddings: "pending",
        },
      },
    ],
    page: 1,
    page_size: 25,
    total: 428,
    pages: 18,
  });
  vi.mocked(executeDocumentPreflight).mockResolvedValue({
    ...detail,
    state: "running",
    stages: [{ ...detail.stages[0], state: "completed" }, detail.stages[1]],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("document runs", () => {
  it("shows version-pinned runs and separate coverage states", async () => {
    render(<DocumentRunsPanel sources={[source]} />);
    expect(await screen.findByText("Fixture documental")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Fixture documental/ }));

    expect(await screen.findByText("312 / 428")).toBeInTheDocument();
    expect(screen.getAllByText("No ejecutado").length).toBeGreaterThan(0);
    expect(screen.getByText("Sin denominador")).toBeInTheDocument();
    expect(screen.queryByText(/comprensión total/i)).toBeNull();
    expect(
      screen.queryByRole("button", {
        name: /Ejecutar OCR|Analizar con IA|Generar embeddings/i,
      }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Siguiente" })).toBeEnabled();
    expect(screen.getByText("Página 1 de 18")).toBeInTheDocument();
  });

  it("exposes only working actions and visible loading errors", async () => {
    render(<DocumentRunsPanel sources={[source]} />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Fixture documental/ }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Ejecutar preflight" }),
    );
    await waitFor(() =>
      expect(executeDocumentPreflight).toHaveBeenCalledWith(run.id),
    );
    expect(
      screen.getByRole("button", { name: "Cancelar" }),
    ).toBeInTheDocument();
  });
});
