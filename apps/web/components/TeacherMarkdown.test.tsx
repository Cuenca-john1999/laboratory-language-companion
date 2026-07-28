import { render, screen } from "@testing-library/react";
import { cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TeacherMarkdown } from "./TeacherMarkdown";

afterEach(cleanup);

describe("TeacherMarkdown", () => {
  it("renders headings, emphasis, lists, quotes and separators", () => {
    const { container } = render(
      <TeacherMarkdown>
        {
          "### Objetivo\n\n**fuerte** y *cursiva*\n\n- eins\n- zwei\n\n> Hinweis\n\n---"
        }
      </TeacherMarkdown>,
    );
    expect(screen.getByRole("heading", { name: "Objetivo" })).toBeVisible();
    expect(screen.getByText("fuerte").tagName).toBe("STRONG");
    expect(screen.getByText("cursiva").tagName).toBe("EM");
    expect(screen.getByRole("list")).toBeVisible();
    expect(container.querySelector("blockquote")).toBeTruthy();
    expect(container.querySelector("hr")).toBeTruthy();
  });

  it("renders GFM tables inside a responsive scrolling region", () => {
    const { container } = render(
      <TeacherMarkdown>
        {"| Caso | Artículo |\n| --- | --- |\n| Akkusativ | den |"}
      </TeacherMarkdown>,
    );
    expect(screen.getByRole("table")).toBeVisible();
    expect(screen.getByText("Akkusativ")).toBeVisible();
    expect(container.querySelector(".teacher-table-scroll")).toHaveAttribute(
      "tabindex",
      "0",
    );
  });

  it("renders inline and fenced code", () => {
    const { container } = render(
      <TeacherMarkdown>
        {"Usa `den`.\n\n```de\nIch sehe den Mann.\n```"}
      </TeacherMarkdown>,
    );
    expect(container.querySelector("p code")).toHaveTextContent("den");
    expect(container.querySelector("pre code")).toHaveTextContent(
      "Ich sehe den Mann.",
    );
  });

  it("renders inline and block formulas", () => {
    const { container } = render(
      <TeacherMarkdown>
        {"Cambio $der \\rightarrow den$.\n\n$$\nein \\rightarrow einen\n$$"}
      </TeacherMarkdown>,
    );
    expect(container.querySelectorAll(".katex")).toHaveLength(2);
    expect(container.querySelector(".katex-display")).toBeTruthy();
  });

  it("secures links and rejects unsafe URLs", () => {
    const { container } = render(
      <TeacherMarkdown>
        {"[Fuente](https://example.com) [Ataque](javascript:alert(1))"}
      </TeacherMarkdown>,
    );
    const safe = screen.getByRole("link", { name: /Fuente/ });
    expect(safe).toHaveAttribute("target", "_blank");
    expect(safe).toHaveAttribute("rel", "noopener noreferrer");
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
  });

  it("does not execute or interpret raw HTML and scripts", () => {
    const { container } = render(
      <TeacherMarkdown>
        {'<img src=x onerror="alert(1)"><script>alert(2)</script>'}
      </TeacherMarkdown>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container).toHaveTextContent("<script>alert(2)</script>");
  });

  it.each([
    "**negrita sin cerrar",
    "| Caso | Forma |\n| --- |",
    "$der \\rightarrow den",
    "```de\nIch sehe den Mann.",
  ])("tolerates partial streaming Markdown: %s", (partial) => {
    const { container } = render(<TeacherMarkdown>{partial}</TeacherMarkdown>);
    expect(container).toHaveTextContent(/.+/);
  });

  it("preserves German characters", () => {
    render(
      <TeacherMarkdown>
        {"Ärztin, Öl, Übung, Straße: **größer**."}
      </TeacherMarkdown>,
    );
    expect(screen.getByText(/Ärztin, Öl, Übung, Straße/)).toBeVisible();
  });
});
