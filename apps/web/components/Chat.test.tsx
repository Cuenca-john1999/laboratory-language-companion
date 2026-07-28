import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Chat } from "./Chat";

vi.mock("../lib/api", () => ({
  getTeacherRoles: vi.fn().mockResolvedValue({
    provider: "lm_studio",
    available: true,
    roles: [
      { role: "teacher", available: true },
      { role: "deep_teacher", available: true },
    ],
    error: null,
  }),
  streamNdjson: vi.fn(),
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
});
