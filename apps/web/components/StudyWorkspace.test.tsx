import type {
  StudyDashboard,
  StudyPath,
  StudySection,
} from "@deutschos/shared";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StudyWorkspace } from "./StudyWorkspace";
import { getStudyDashboard, getStudyHistory, getStudyPath } from "../lib/api";

vi.mock("../lib/api", () => ({
  askStudyTeacher: vi.fn(),
  createStudyNote: vi.fn(),
  createStudyQuestion: vi.fn(),
  createStudyWorkbookLink: vi.fn(),
  deleteStudyNote: vi.fn(),
  deleteStudyQuestion: vi.fn(),
  getStudyDashboard: vi.fn(),
  getStudyHistory: vi.fn(),
  getStudyNotes: vi.fn(),
  getStudyPath: vi.fn(),
  getStudyQuestions: vi.fn(),
  operationId: vi.fn(() => "test-operation-id"),
  reviewCanonicalStudyTopic: vi.fn(),
  reviewStudyWorkbookLink: vi.fn(),
  startStudySession: vi.fn(),
  transitionStudySession: vi.fn(),
  updateStudyNote: vi.fn(),
  updateStudyPosition: vi.fn(),
  updateStudyPreferences: vi.fn(),
  updateStudyQuestion: vi.fn(),
  updateStudyWorkbookLink: vi.fn(),
}));

const section = (number: number): StudySection => ({
  id: number,
  stable_key: `herder:theme:${number}`,
  order: number,
  title: `Tema ${number}`,
  topic: `Tema ${number}`,
  source_id: "herder",
  source_version: 1,
  source_name: "Herder",
  pdf_page_start: number,
  pdf_page_end: number,
  printed_page_label: String(number),
  editorial_status: "system_verified",
  practical_status: "not_started",
  current_pdf_page: null,
  selection_origin: null,
  last_activity_at: null,
  last_session_id: null,
  open_questions: 0,
  workbook_link: null,
  theme_number: number,
  title_es: `Tema ${number}`,
  title_de: null,
  printed_page_start: number,
  printed_page_end: number,
  printed_range_status: "calculated",
  reference_pdf_page: number,
  manual_scan_layout: "single_page",
  manual_region: "full",
  outline: [],
});

const path: StudyPath = {
  source_id: "herder",
  source_version: 1,
  source_name: "Herder",
  workbook_source_id: null,
  workbook_source_name: null,
  sections: Array.from({ length: 51 }, (_, index) => section(index + 1)),
};

const dashboard: StudyDashboard = {
  path_ready: true,
  total_sections: 51,
  active_session: null,
  recommendation: {
    kind: "section",
    reason: "Es la siguiente sección disponible en Herder.",
    session_id: null,
    section_stable_key: path.sections[0].stable_key,
  },
  open_questions: 0,
  recent_sessions: [],
  preferences: {
    mission_preference: "automatic",
    active_source_id: null,
    active_section_stable_key: null,
  },
};

beforeEach(() => {
  vi.mocked(getStudyDashboard).mockResolvedValue(dashboard);
  vi.mocked(getStudyPath).mockResolvedValue(path);
  vi.mocked(getStudyHistory).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Study local loading states", () => {
  it("shows a useful empty history without treating it as an error", async () => {
    render(<StudyWorkspace />);
    await screen.findByText("51");

    fireEvent.click(screen.getByRole("button", { name: "Historial" }));

    expect(
      screen.getByRole("heading", {
        name: "Todavía no hay sesiones de estudio.",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Elegir un tema" }),
    ).toBeEnabled();
  });

  it("keeps the canonical route available when dashboard and history fail", async () => {
    vi.mocked(getStudyDashboard).mockRejectedValue(
      new Error("No se pudo cargar el resumen."),
    );
    vi.mocked(getStudyHistory).mockRejectedValue(
      new Error("No se pudo cargar el historial."),
    );

    render(<StudyWorkspace />);
    await screen.findByText("51");

    fireEvent.click(screen.getByRole("button", { name: "Ruta Herder" }));
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Estudiar" })).toHaveLength(
        51,
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Historial" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "No se pudo cargar el historial.",
    );
    expect(
      screen.getByRole("button", { name: "Abrir la ruta Herder" }),
    ).toBeEnabled();
  });
});
