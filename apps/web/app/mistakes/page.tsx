import type { Mistake } from "@deutschos/shared";

import { getMistakes } from "../../lib/api";

export default async function MistakesPage() {
  let rows: Mistake[];
  try {
    rows = await getMistakes();
  } catch (error) {
    return (
      <section>
        <p className="eyebrow">MEMORIA DE ERRORES</p>
        <h1>No se pudo cargar la memoria de errores.</h1>
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
      <p className="eyebrow">MEMORIA DE ERRORES</p>
      <h1>Aprender de lo que cuesta.</h1>
      {rows.length ? (
        rows.map((mistake) => (
          <article className="mistake" key={mistake.id}>
            <small>
              {mistake.category} · {mistake.status}
            </small>
            <del>{mistake.original_text}</del>
            <strong>{mistake.corrected_text}</strong>
            <p>{mistake.explanation_es}</p>
          </article>
        ))
      ) : (
        <div className="empty">
          <h2>Todavía no hay errores registrados.</h2>
          <p>
            La extracción automática pertenece a un hito posterior. Aquí no
            aparecerán errores inventados.
          </p>
        </div>
      )}
    </section>
  );
}
