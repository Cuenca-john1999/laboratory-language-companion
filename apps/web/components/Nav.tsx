import Link from "next/link";

export function Nav() {
  return (
    <header className="nav">
      <Link className="brand" href="/">
        <span>Deutsch</span>OS
      </Link>
      <nav>
        <Link href="/">Übersicht</Link>
        <Link href="/progress">Plan</Link>
        <Link href="/skills">Fähigkeiten</Link>
        <Link href="/chat">Lehrer</Link>
        <Link href="/mistakes">Fehler</Link>
        <Link href="/profile">Profil</Link>
      </nav>
    </header>
  );
}
