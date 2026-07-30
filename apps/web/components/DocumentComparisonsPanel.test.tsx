import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  confirmPageCorrespondence,
  createVersionComparison,
  executeVersionComparison,
  getDocumentVersions,
  getPageCorrespondences,
  getVersionComparisonEvents,
  getVersionComparisons,
} from "../lib/api";
import { DocumentComparisonsPanel } from "./DocumentComparisonsPanel";

vi.mock("../lib/api", () => ({
  adjustPageCorrespondence: vi.fn(),
  cancelVersionComparison: vi.fn(),
  confirmPageCorrespondence: vi.fn(),
  createVersionComparison: vi.fn(),
  executeVersionComparison: vi.fn(),
  getDocumentVersions: vi.fn(),
  getComparisonThumbnailUrl: vi.fn(
    () => "http://127.0.0.1:8000/fixture-thumbnail.svg",
  ),
  getPageCorrespondences: vi.fn(),
  getVersionComparisons: vi.fn(),
  getVersionComparisonEvents: vi.fn(),
  markPageWithoutEquivalent: vi.fn(),
  rejectPageCorrespondence: vi.fn(),
  revertVersionComparisonEvent: vi.fn(),
}));

const source = {
  id: "fixture-source",
  title: "Documento sintético",
  collection: null,
  format: ".pdf",
  current_path: "fixture.pdf",
  source_status: "present",
  document_state: "candidate" as const,
  needs_manual_review: false,
  active_version_id: 1,
  active_version_number: 1,
  latest_version_id: 2,
  latest_version_number: 2,
  page_count: 6,
  active_chunks: 0,
  active_embeddings: 0,
  needs_ocr: false,
  error_code: null,
  change_pending: true,
};

const comparison = {
  id: "comparison-1",
  source_id: source.id,
  source_title: source.title,
  base_source_version_id: 1,
  base_version_number: 1,
  target_source_version_id: 2,
  target_version_number: 2,
  base_hash: "a".repeat(64),
  target_hash: "b".repeat(64),
  algorithm_version: "page-match.v1",
  configuration: { window: 12 },
  configuration_hash: "c".repeat(64),
  revision: 1,
  supersedes_comparison_id: null,
  state: "completed_with_issues" as const,
  initiated_by: "fixture",
  summary: {
    base_pages: 4,
    target_pages: 6,
    one_to_one: 2,
    one_to_many: 2,
    pending: 4,
  },
  error_code: null,
  error_detail: null,
  needs_review: true,
  transfer_plan_revision: 1,
  created_at: "2026-07-30T00:00:00Z",
  started_at: "2026-07-30T00:00:00Z",
  completed_at: "2026-07-30T00:00:01Z",
  updated_at: "2026-07-30T00:00:01Z",
};

const relation = {
  id: "relation-1",
  comparison_id: comparison.id,
  relation_type: "one_to_many",
  review_state: "needs_review",
  confidence: "high",
  base_pages: [2],
  target_pages: [2, 3],
  text_similarity: 0.91,
  visual_similarity: 0.96,
  geometry_similarity: 0.5,
  ordinal_similarity: 0.9,
  printed_page_similarity: 1,
  split_similarity: 0.95,
  aggregate_score: 0.94,
  evidence: { explanation_code: "double_page_regions_match" },
  text_difference: { recommendation: "manual_review" },
  recommendation: "manual_review",
  review_note: null,
  created_by: "algorithm",
  created_at: "2026-07-30T00:00:00Z",
  updated_at: "2026-07-30T00:00:00Z",
};

beforeEach(() => {
  vi.mocked(getVersionComparisons).mockResolvedValue([comparison]);
  vi.mocked(getDocumentVersions).mockResolvedValue([
    {
      id: 1,
      source_id: source.id,
      version_number: 1,
      content_hash: "a".repeat(64),
      size_bytes: 100,
      mtime_ns: 1,
      observed_path: "fixture.pdf",
      observed_name: "fixture.pdf",
      detected_at: "2026-07-30T00:00:00Z",
      page_count: 4,
      document_state: "historical",
      availability_state: "present",
      extraction_state: "processed",
      chunk_state: "pending",
      embedding_state: "pending",
      activation_state: "historical",
      is_active: false,
      extractor: null,
      extractor_version: null,
      extraction_tool: null,
      extraction_tool_version: null,
      ocr_tool: null,
      ocr_tool_version: null,
      ocr_languages: [],
      technical_metadata: {},
      provenance: "fixture",
      change_reason: null,
      previous_version_id: null,
      error_code: null,
      error_detail: null,
      statistics: {},
      processed_at: null,
      chunks: 0,
      embeddings: 0,
    },
    {
      id: 2,
      source_id: source.id,
      version_number: 2,
      content_hash: "b".repeat(64),
      size_bytes: 120,
      mtime_ns: 2,
      observed_path: "fixture.pdf",
      observed_name: "fixture.pdf",
      detected_at: "2026-07-30T00:00:00Z",
      page_count: 6,
      document_state: "candidate",
      availability_state: "present",
      extraction_state: "processed",
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
      provenance: "fixture",
      change_reason: null,
      previous_version_id: 1,
      error_code: null,
      error_detail: null,
      statistics: {},
      processed_at: null,
      chunks: 0,
      embeddings: 0,
    },
  ]);
  vi.mocked(getPageCorrespondences).mockResolvedValue({
    items: [relation],
    page: 1,
    page_size: 50,
    total: 1,
    pages: 1,
  });
  vi.mocked(getVersionComparisonEvents).mockResolvedValue([
    {
      id: "event-1",
      comparison_id: comparison.id,
      correspondence_id: relation.id,
      event_type: "comparison_completed",
      actor: "fixture",
      previous_state: "running",
      new_state: "completed_with_issues",
      detail: {},
      created_at: "2026-07-30T00:00:00Z",
    },
  ]);
  vi.mocked(createVersionComparison).mockResolvedValue(comparison);
  vi.mocked(executeVersionComparison).mockResolvedValue(comparison);
  vi.mocked(confirmPageCorrespondence).mockResolvedValue({
    ...relation,
    review_state: "confirmed",
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("document comparisons", () => {
  it("shows exact versions and a paginated one-to-many map", async () => {
    render(<DocumentComparisonsPanel sources={[source]} />);
    expect(
      await screen.findByRole("heading", { name: "Comparaciones" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Fuente para comparar"), {
      target: { value: source.id },
    });
    expect(
      (await screen.findAllByText(/v1 · aaaaaaaaaa · 4 págs/)).length,
    ).toBe(2);
    fireEvent.click(
      screen.getByRole("button", { name: /Documento sintético/ }),
    );
    expect(
      await screen.findByText("Mapa de correspondencias"),
    ).toBeInTheDocument();
    expect(screen.getByText("Base 2")).toBeInTheDocument();
    expect(screen.getByText("Objetivo 2–3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /OCR|IA/i })).toBeNull();
  });

  it("confirms a real proposal and exposes loading-safe execution", async () => {
    render(<DocumentComparisonsPanel sources={[source]} />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Documento sintético/ }),
    );
    fireEvent.click(await screen.findByText("Base 2"));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));
    await waitFor(() =>
      expect(confirmPageCorrespondence).toHaveBeenCalledWith(
        comparison.id,
        relation.id,
      ),
    );
    expect(await screen.findByText("confirmed")).toBeInTheDocument();
  });
});
