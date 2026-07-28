"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const sections = [
  { href: "/", label: "Übersicht" },
  { href: "/study", label: "Estudio" },
  { href: "/progress", label: "Plan" },
  { href: "/skills", label: "Fähigkeiten" },
  { href: "/chat", label: "Lehrer" },
  { href: "/library", label: "Bibliothek" },
  { href: "/mistakes", label: "Fehler" },
  { href: "/profile", label: "Profil" },
] as const;

export function Nav() {
  const pathname = usePathname();

  return (
    <header className="nav">
      <Link className="brand" href="/">
        <span>Deutsch</span>OS
      </Link>
      <nav aria-label="Navegación principal">
        {sections.map(({ href, label }) => {
          const active =
            href === "/" ? pathname === href : pathname.startsWith(href);
          return (
            <Link
              aria-current={active ? "page" : undefined}
              className={active ? "active" : undefined}
              href={href}
              key={href}
            >
              {label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
