import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
import { DocumentAuditPanel } from "./DocumentAuditPanel";

vi.mock("../lib/api", () => ({
  applyDocumentAuditDecisions: vi.fn(),
  createDocumentAuditExport: vi.fn(),
  createDocumentClosureSnapshot: vi.fn(),
  dryRunDocumentAuditDecisions: vi.fn(),
  getDocumentClosureSnapshots: vi.fn(),
  getDocumentClosureStatus: vi.fn(),
  getDocumentReviewSummary: vi.fn(),
  previewDocumentAuditExport: vi.fn(),
  validateDocumentAuditDecisions: vi.fn(),
}));

const summaryItem = {
  source_id: "grammar",
  source_version_id: 584,
  source_title: "Herder · Gramática",
  version_number: 3,
  hash: "a".repeat(64),
  activation_state: "candidate",
  run_id: "run-1",
  run_state: "complete",
  readiness: "structurally_ready_with_issues",
  metrics: {},
  queue: { P0: 0, P1: 146, P2: 0 },
  blockers: [],
};

const decisionSet = {
  decision_schema: "audit-decisions.v1" as const,
  package_logical_hash: "b".repeat(64),
  source_id: "grammar",
  source_version_id: 584,
  document_hash: "a".repeat(64),
  reviewer: "Auditor externo",
  reviewed_at: "2026-08-10T00:00:00Z",
  decisions: [{ target_type: "candidate" }],
};

beforeEach(() => {
  vi.mocked(getDocumentReviewSummary).mockResolvedValue({
    items: [summaryItem],
  });
  vi.mocked(getDocumentClosureStatus).mockResolvedValue({
    source_id: "grammar",
    source_version_id: 584,
    source_title: "Herder · Gramática",
    document_hash: "a".repeat(64),
    activation_state: "candidate",
    structural_readiness: "structurally_ready_with_issues",
    ai_readiness: "ready_for_ai_with_issues",
    blockers: [],
    queue: { P0: 0, P1: 146, P2: 0 },
    unresolved_topics: 0,
    visual_pending: 4,
    latest_snapshot: null,
  });
  vi.mocked(getDocumentClosureSnapshots).mockResolvedValue([]);
  vi.mocked(previewDocumentAuditExport).mockResolvedValue({
    source_version_id: 584,
    mode: "full",
    selection: {},
    include_visuals: false,
    counts: { candidates: 25, pages: 10 },
    estimated_size_bytes: 2048,
    visual_asset_count: 0,
    affected_pages: [1, 2],
    readiness: "structurally_ready_with_issues",
    ai_readiness: "ready_for_ai_with_issues",
  });
  const valid = {
    valid: true,
    decision_set_hash: "c".repeat(64),
    applicable: [{ target_id: "candidate-1" }],
    stale: [],
    conflicts: [],
    invalid: [],
    no_effect: [],
    estimated_impact: { candidates: 1 },
  };
  vi.mocked(validateDocumentAuditDecisions).mockResolvedValue(valid);
  vi.mocked(dryRunDocumentAuditDecisions).mockResolvedValue(valid);
  vi.mocked(applyDocumentAuditDecisions).mockResolvedValue({
    id: "import-1",
    source_id: "grammar",
    source_version_id: 584,
    reviewer: "Auditor externo",
    state: "applied",
    decision_count: 1,
    applied_count: 1,
    created_at: "2026-08-10T00:00:00Z",
    applied_at: "2026-08-10T00:00:00Z",
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("document audit panel", () => {
  it("shows concise readiness and requires preview before creating an export", async () => {
    render(<DocumentAuditPanel />);

    expect(
      await screen.findByText("Lista para IA con pendientes"),
    ).toBeInTheDocument();
    expect(screen.getByText("146 / 0")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Preview · Auditoría completa" }),
    );
    expect(
      await screen.findByLabelText("Preview de exportación"),
    ).toHaveTextContent("2 páginas");
    expect(createDocumentAuditExport).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Crear paquete" }));
    await waitFor(() =>
      expect(createDocumentAuditExport).toHaveBeenCalledOnce(),
    );
  });

  it("selects JSON without applying, then validates, dry-runs and confirms atomic apply", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<DocumentAuditPanel />);
    await screen.findByText("Lista para IA con pendientes");
    const file = new File([JSON.stringify(decisionSet)], "decisions.json", {
      type: "application/json",
    });
    Object.defineProperty(file, "text", {
      value: () => Promise.resolve(JSON.stringify(decisionSet)),
    });
    fireEvent.change(screen.getByLabelText("Archivo de decisiones (.json)"), {
      target: { files: [file] },
    });

    expect(
      await screen.findByText(/aún no se ha aplicado/),
    ).toBeInTheDocument();
    expect(applyDocumentAuditDecisions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "1. Validar" }));
    expect(await screen.findByText(/Validación: válida/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "2. Dry-run" }));
    expect(
      await screen.findByText(/Dry-run: 1 aplicables/),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "3. Confirmar y aplicar" }),
    );
    await waitFor(() =>
      expect(applyDocumentAuditDecisions).toHaveBeenCalledWith(decisionSet),
    );
    expect(confirm).toHaveBeenCalledOnce();
  });

  it("shows snapshot history and creates a new immutable snapshot explicitly", async () => {
    vi.mocked(getDocumentClosureSnapshots).mockResolvedValue([
      {
        id: "snap-1",
        source_id: "grammar",
        source_version_id: 584,
        document_hash: "a".repeat(64),
        structural_readiness: "structurally_ready_with_issues",
        ai_readiness: "ready_for_ai_with_issues",
        state: "ready_with_issues",
        snapshot_hash: "d".repeat(64),
        created_at: "2026-08-10T00:00:00Z",
      },
    ]);
    vi.mocked(createDocumentClosureSnapshot).mockResolvedValue({
      id: "snap-2",
      source_id: "grammar",
      source_version_id: 584,
      document_hash: "a".repeat(64),
      structural_readiness: "structurally_ready_with_issues",
      ai_readiness: "ready_for_ai_with_issues",
      state: "ready_with_issues",
      snapshot_hash: "e".repeat(64),
      created_at: "2026-08-10T00:01:00Z",
    });
    render(<DocumentAuditPanel />);

    expect(await screen.findByText(/dddddddddddd/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Crear snapshot" }));
    await waitFor(() =>
      expect(createDocumentClosureSnapshot).toHaveBeenCalledWith(584),
    );
  });
});
