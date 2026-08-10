import type { Session } from "@llc/shared";

import { getSessions } from "../../lib/api";
import { formatLocalDateTime } from "../../lib/format";

export default async function SessionsPage() {
  let rows: Session[];
  try {
    rows = await getSessions();
  } catch (error) {
    return (
      <section>
        <p className="eyebrow">HISTORIAL LOCAL</p>
        <h1>No se pudo cargar el historial.</h1>
        <div className="error" role="alert">
          {error instanceof Error
            ? error.message
            : "Comprueba que la API local esté iniciada."}
        </div>
      </section>
    );
  }

  return (
    <section>
      <p className="eyebrow">HISTORIAL LOCAL</p>
      <h1>Sesiones</h1>
      <div className="list">
        {rows.length ? (
          rows.map((session) => (
            <article className="session" key={session.id}>
              <span>{formatLocalDateTime(session.started_at)}</span>
              <strong>{session.session_type}</strong>
              <small>{session.model_used ?? "Modelo no registrado"}</small>
              {session.summary ? <p>{session.summary}</p> : null}
            </article>
          ))
        ) : (
          <div className="empty">
            <p>Las conversaciones completadas aparecerán aquí.</p>
          </div>
        )}
      </div>
    </section>
  );
}
