import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Chat } from "./Chat";
import { streamNdjson } from "../lib/api";
import { refreshModelAvailability } from "../lib/modelAvailability";

vi.mock("../lib/api", () => ({
  streamNdjson: vi.fn(),
}));

vi.mock("../lib/modelAvailability", () => ({
  refreshModelAvailability: vi.fn(),
  useModelAvailability: vi.fn().mockReturnValue({
    status: "ready",
    data: {
      lm_studio_available: true,
      installed_models: [],
      policy_version: "library-model-routing.v1",
      roles: [
        {
          role: "teacher",
          available: true,
          configured_model: "teacher",
          selected_model: "teacher",
          fallback_models: [],
        },
        {
          role: "deep",
          available: true,
          configured_model: "deep",
          selected_model: "deep",
          fallback_models: [],
        },
      ],
    },
    error: null,
  }),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Chat trust boundary", () => {
  it("renders untrusted user Markdown and HTML only as text", async () => {
    const { container } = render(<Chat />);
    const textarea = await screen.findByPlaceholderText("Schreib etwas…");
    fireEvent.change(textarea, {
      target: { value: "### Objetivo inyectado <script>alert(1)</script>" },
    });
    fireEvent.submit(textarea.closest("form")!);

    await waitFor(() =>
      expect(container.querySelector(".bubble.user")).toHaveTextContent(
        "### Objetivo inyectado <script>alert(1)</script>",
      ),
    );
    expect(container.querySelector(".bubble.user h3")).toBeNull();
    expect(container.querySelector(".bubble.user script")).toBeNull();
  });

  it("refreshes shared availability immediately when the teacher role changes", async () => {
    render(<Chat />);
    const role = await screen.findByRole("combobox", {
      name: "Rol del profesor",
    });

    fireEvent.change(role, { target: { value: "deep_teacher" } });

    expect(role).toHaveValue("deep_teacher");
    expect(refreshModelAvailability).toHaveBeenCalledWith(0);
    expect(screen.getByPlaceholderText("Schreib etwas…")).toBeEnabled();
  });
});

async function* events(...items: unknown[]) {
  for (const item of items) {
    yield item;
  }
}

describe("Chat empty-response recovery", () => {
  it("renders one streamed Markdown answer after server recovery", async () => {
    vi.mocked(streamNdjson).mockReturnValue(
      events(
        { type: "token", content: "**Recuperado.**" },
        {
          type: "done",
          model: "teacher",
          session_id: 7,
          attempt_count: 2,
          recovery: "empty_visible_content",
          finish_reason: "stop",
        },
      ) as ReturnType<typeof streamNdjson>,
    );
    const { container } = render(<Chat />);
    const textarea = await screen.findByPlaceholderText("Schreib etwas…");

    fireEvent.change(textarea, { target: { value: "Hilf mir" } });
    fireEvent.submit(textarea.closest("form")!);

    await screen.findByText("Recuperado.");
    expect(container.querySelectorAll(".bubble.teacher")).toHaveLength(1);
    expect(container.querySelector(".bubble.teacher strong")).toHaveTextContent(
      "Recuperado.",
    );
    expect(screen.queryByText(/reasoning_content/i)).not.toBeInTheDocument();
  });

  it("shows a manual retry after two empty attempts and adds no empty bubble", async () => {
    vi.mocked(streamNdjson)
      .mockReturnValueOnce(
        events({
          type: "error",
          detail:
            "El profesor no pudo generar una respuesta visible. Puedes volver a intentarlo.",
          retryable: true,
        }) as ReturnType<typeof streamNdjson>,
      )
      .mockReturnValueOnce(
        events(
          { type: "token", content: "Ahora sí." },
          {
            type: "done",
            model: "teacher",
            session_id: 8,
            attempt_count: 1,
            recovery: null,
            finish_reason: "stop",
          },
        ) as ReturnType<typeof streamNdjson>,
      );
    const { container } = render(<Chat />);
    const textarea = await screen.findByPlaceholderText("Schreib etwas…");

    fireEvent.change(textarea, { target: { value: "Versuch" } });
    fireEvent.submit(textarea.closest("form")!);

    const retry = await screen.findByRole("button", {
      name: "Volver a intentar",
    });
    expect(container.querySelector(".bubble.teacher")).toBeNull();
    expect(container.querySelectorAll(".bubble.user")).toHaveLength(1);

    fireEvent.click(retry);

    await screen.findByText("Ahora sí.");
    expect(container.querySelectorAll(".bubble.teacher")).toHaveLength(1);
    expect(container.querySelectorAll(".bubble.user")).toHaveLength(1);
    expect(vi.mocked(streamNdjson)).toHaveBeenCalledTimes(2);
  });

  it("cancels the active request without leaving an error or starting another", async () => {
    vi.mocked(streamNdjson).mockImplementation(async function* (_path, init) {
      await new Promise<void>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    });
    const { container } = render(<Chat />);
    const textarea = await screen.findByPlaceholderText("Schreib etwas…");

    fireEvent.change(textarea, { target: { value: "Stoppen" } });
    fireEvent.submit(textarea.closest("form")!);
    const cancel = await screen.findByRole("button", { name: "Cancelar" });
    fireEvent.click(cancel);

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Cancelar" })).toBeNull(),
    );
    expect(container.querySelector(".error")).toBeNull();
    expect(container.querySelector(".bubble.teacher")).toBeNull();
    expect(vi.mocked(streamNdjson)).toHaveBeenCalledTimes(1);
  });
});
