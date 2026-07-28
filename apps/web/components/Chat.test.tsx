import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Chat } from "./Chat";
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
