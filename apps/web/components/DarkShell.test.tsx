import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Nav } from "./Nav";

let pathname = "/study";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
}));

afterEach(cleanup);

describe("dark application shell", () => {
  it("keeps every route and identifies the current section", () => {
    render(<Nav />);

    const navigation = screen.getByRole("navigation", {
      name: "Navegación principal",
    });
    expect(navigation.querySelectorAll("a")).toHaveLength(9);
    expect(screen.getByRole("link", { name: "Estudio" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Lehrer" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(
      screen.getByRole("link", { name: "Datos y memoria" }),
    ).toHaveAttribute("href", "/data");
  });

  it("marks only the dashboard active at the root route", () => {
    pathname = "/";
    render(<Nav />);

    expect(screen.getByRole("link", { name: "Übersicht" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getAllByRole("link", { current: "page" })).toHaveLength(1);
    pathname = "/study";
  });

  it("declares one dark scheme, semantic tokens and reduced motion", () => {
    const css = readFileSync(join(process.cwd(), "app", "globals.css"), "utf8");
    const layout = readFileSync(
      join(process.cwd(), "app", "layout.tsx"),
      "utf8",
    );

    expect(css).toContain("color-scheme: dark");
    expect(layout).toContain('colorScheme: "dark"');
    expect(layout).toContain("export const viewport");
    expect(layout).toContain('themeColor: "#0b0f16"');
    expect(css).toContain("--background:");
    expect(css).toContain("--surface:");
    expect(css).toContain("--text-primary:");
    expect(css).toContain("--accent:");
    expect(css).toContain("--transition-fast:");
    expect(css).toContain(":focus-visible");
    expect(css).toContain(":disabled");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(`${css}\n${layout}`).not.toMatch(
      /theme.?toggle|selector de tema|prefers-color-scheme:\s*light/i,
    );
  });
});
