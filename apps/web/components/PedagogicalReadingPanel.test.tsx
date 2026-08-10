import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  controlPedagogicalReadingRun,
  createPedagogicalReadingRun,
  getPedagogicalReadingReadiness,
  getPedagogicalReadingRuns,
} from "../lib/api";
import { PedagogicalReadingPanel } from "./PedagogicalReadingPanel";

vi.mock("../lib/api", () => ({
  controlPedagogicalReadingRun: vi.fn(),
  createPedagogicalReadingRun: vi.fn(),
  getPedagogicalReadingReadiness: vi.fn(),
  getPedagogicalReadingRuns: vi.fn(),
}));

const readiness = {
  source_version_id: 584,
  source_id: "herder",
  title: "Herder Grammar",
  document_hash: "a".repeat(64),
  activation_state: "candidate",
  is_active: false,
  ai_readiness: "ready_for_ai_with_issues",
  topic_count: 51,
  ready: true,
};

const run = {
  id: "reading-1",
  source_version_id: 584,
  document_hash: readiness.document_hash,
  language: "de",
  pass_number: 1,
  model_role: "deep",
  resolved_model: "qwen3-30b",
  state: "planned",
  pause_reason: null,
  stop_reason: null,
  auto_continue: false,
  stages: [],
};

beforeEach(() => {
  vi.mocked(getPedagogicalReadingReadiness).mockResolvedValue(readiness);
  vi.mocked(getPedagogicalReadingRuns).mockResolvedValue([]);
  vi.mocked(createPedagogicalReadingRun).mockResolvedValue(run);
  vi.mocked(controlPedagogicalReadingRun).mockResolvedValue(run);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("pedagogical reading panel", () => {
  it("shows source readiness and keeps automatic continuation disabled by default", async () => {
    render(<PedagogicalReadingPanel />);
    expect(
      await screen.findByRole("heading", { name: "AI Reading" }),
    ).toBeInTheDocument();
    expect(screen.getByText("51 topics")).toBeInTheDocument();
    expect(screen.getByText("Not started")).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", {
        name: /Automatic iterative continuation/,
      }),
    ).not.toBeChecked();
  });

  it("creates a planned run and starts it only after the explicit button action", async () => {
    render(<PedagogicalReadingPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Start" }));
    await waitFor(() =>
      expect(createPedagogicalReadingRun).toHaveBeenCalledWith(false),
    );
    expect(controlPedagogicalReadingRun).toHaveBeenCalledWith(
      "reading-1",
      "start",
    );
  });

  it("exposes progress, retry and next-pass controls without a bulk confirm action", async () => {
    vi.mocked(getPedagogicalReadingRuns).mockResolvedValue([
      {
        ...run,
        state: "completed_with_issues",
        stages: [
          {
            id: "stage-1",
            run_id: run.id,
            topic_number: 1,
            state: "completed",
            attempts: 1,
            target_reasons: ["broad_pass_1"],
          },
          {
            id: "stage-2",
            run_id: run.id,
            topic_number: 2,
            state: "failed",
            attempts: 1,
            target_reasons: ["broad_pass_1"],
          },
        ],
      },
    ]);
    render(<PedagogicalReadingPanel />);
    expect(
      await screen.findByText("Completed with issues"),
    ).toBeInTheDocument();
    expect(screen.getByText("1 completed")).toBeInTheDocument();
    expect(screen.getByText("1 failed")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Retry failed" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Prepare next pass" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /confirm/i }),
    ).not.toBeInTheDocument();
  });
});
