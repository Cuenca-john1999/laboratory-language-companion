import type { StudyData } from "@deutschos/shared";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearStudySessions,
  deleteStudySession,
  deleteStudySessions,
  getStudyData,
} from "../lib/api";
import { DataMemoryWorkspace } from "./DataMemoryWorkspace";

vi.mock("../lib/api", () => ({
  clearStudySessions: vi.fn(),
  deleteStudySession: vi.fn(),
  deleteStudySessions: vi.fn(),
  getStudyData: vi.fn(),
}));

const sessions: StudyData["sessions"] = [
  {
    id: "d9c59afb-d4d5-409b-9517-c48a36f60337",
    status: "completed",
    section_title: "Tema 1",
    concept_name: "Pronombres",
    mission_label: "Estudio directo",
    active_seconds: 600,
    started_at: "2026-07-21T23:56:34Z",
    updated_at: "2026-07-21T23:58:34Z",
  },
  {
    id: "abeaed6d-561c-4301-aa06-d7bdda50f59f",
    status: "abandoned",
    section_title: "Tema 2",
    concept_name: "Acusativo",
    mission_label: "Laboratorio",
    active_seconds: 180,
    started_at: "2026-07-21T23:59:04Z",
    updated_at: "2026-07-22T00:01:04Z",
  },
];

const data: StudyData = {
  total_sessions: 2,
  sessions,
  memory: {
    route_topics: 51,
    started_topics: 1,
    student_skills: 0,
    skill_evidence: 0,
    saved_notes: 0,
    saved_questions: 0,
    active_session_id: null,
    preferences_persisted: true,
    mission_preference: "automatic",
  },
};

const empty: StudyData = {
  ...data,
  total_sessions: 0,
  sessions: [],
};

beforeEach(() => {
  vi.mocked(getStudyData).mockResolvedValue(data);
  vi.mocked(deleteStudySession).mockResolvedValue({ deleted_sessions: 1 });
  vi.mocked(deleteStudySessions).mockResolvedValue({ deleted_sessions: 2 });
  vi.mocked(clearStudySessions).mockResolvedValue({ deleted_sessions: 2 });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Datos y memoria", () => {
  it("renders sessions and durable memory as separate areas", async () => {
    render(<DataMemoryWorkspace />);

    expect(
      await screen.findByRole("heading", { name: "Sesiones de estudio" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Memoria de aprendizaje" }),
    ).toBeInTheDocument();
    expect(screen.getByText("51")).toBeInTheDocument();
    expect(
      screen.getByText(/Borrar sesiones no reinicia automáticamente/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /reiniciar.*memoria/i }),
    ).not.toBeInTheDocument();
  });

  it("keeps memory visible with an empty session history", async () => {
    vi.mocked(getStudyData).mockResolvedValue(empty);
    render(<DataMemoryWorkspace />);

    expect(
      await screen.findByRole("heading", {
        name: "No hay sesiones de estudio guardadas.",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Memoria de aprendizaje" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ir a Study" })).toHaveAttribute(
      "href",
      "/study",
    );
  });

  it("requires an accessible confirmation before deleting one session", async () => {
    vi.mocked(getStudyData)
      .mockResolvedValueOnce(data)
      .mockResolvedValueOnce(empty);
    render(<DataMemoryWorkspace />);
    await screen.findByText("Pronombres · Estudio directo");

    fireEvent.click(screen.getAllByRole("button", { name: "Eliminar" })[0]);
    const dialog = screen.getByRole("dialog", {
      name: "Eliminar “Tema 1”",
    });
    expect(dialog).toHaveTextContent(
      "Tu progreso, habilidades, evidencias, preferencias y ruta de aprendizaje se conservarán.",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Eliminar definitivamente" }),
    );

    await waitFor(() =>
      expect(deleteStudySession).toHaveBeenCalledWith(sessions[0].id),
    );
    expect(
      await screen.findByText("Se eliminó 1 sesión de Study."),
    ).toBeInTheDocument();
  });

  it("deletes a multi-selection and never submits an empty selection", async () => {
    vi.mocked(getStudyData)
      .mockResolvedValueOnce(data)
      .mockResolvedValueOnce(empty);
    render(<DataMemoryWorkspace />);
    await screen.findByText("Tema 1");

    const selectionButton = screen.getByRole("button", {
      name: "Eliminar selección",
    });
    expect(selectionButton).toBeDisabled();
    fireEvent.click(selectionButton);
    expect(deleteStudySessions).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText("Seleccionar Tema 1"));
    fireEvent.click(screen.getByLabelText("Seleccionar Tema 2"));
    fireEvent.click(selectionButton);
    expect(
      screen.getByRole("dialog", { name: "Eliminar 2 sesiones" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Eliminar definitivamente" }),
    );

    await waitFor(() =>
      expect(deleteStudySessions).toHaveBeenCalledWith([
        sessions[0].id,
        sessions[1].id,
      ]),
    );
  });

  it("uses reinforced confirmation to clear every session", async () => {
    vi.mocked(getStudyData)
      .mockResolvedValueOnce(data)
      .mockResolvedValueOnce(empty);
    render(<DataMemoryWorkspace />);
    await screen.findByText("Tema 1");

    fireEvent.click(screen.getByRole("button", { name: "Vaciar historial" }));
    const dialog = screen.getByRole("dialog", {
      name: "Vaciar todo el historial",
    });
    const finalButton = screen.getAllByRole("button", {
      name: "Vaciar historial",
    })[1];
    expect(finalButton).toBeDisabled();
    fireEvent.change(
      screen.getByLabelText(/Escribe VACIAR HISTORIAL para continuar/),
      { target: { value: "VACIAR HISTORIAL" } },
    );
    expect(finalButton).toBeEnabled();
    fireEvent.click(finalButton);

    await waitFor(() => expect(clearStudySessions).toHaveBeenCalledOnce());
    expect(dialog).not.toBeInTheDocument();
  });

  it("shows deletion errors without hiding learning memory", async () => {
    vi.mocked(deleteStudySession).mockRejectedValue(
      new Error("No se pudo eliminar la sesión."),
    );
    render(<DataMemoryWorkspace />);
    await screen.findByText("Tema 1");

    fireEvent.click(screen.getAllByRole("button", { name: "Eliminar" })[0]);
    fireEvent.click(
      screen.getByRole("button", { name: "Eliminar definitivamente" }),
    );

    expect(
      await screen.findByRole("alert", {
        name: "",
      }),
    ).toHaveTextContent("No se pudo eliminar la sesión.");
    expect(
      screen.getByRole("heading", { name: "Memoria de aprendizaje" }),
    ).toBeInTheDocument();
  });
});
