import type { LearningSkill } from "@deutschos/shared";

import { formatLocalDateTime, formatPercent } from "../lib/format";
import { outcomeLabel, skillCategoryLabel, skillName } from "../lib/learning";

export function LearningSkills({ skills }: { skills: LearningSkill[] }) {
  return (
    <section aria-labelledby="skill-evidence">
      <p className="eyebrow">HABILIDADES</p>
      <h2 id="skill-evidence">Evidencia registrada por habilidad</h2>
      <p>
        Dominio y confianza son estimaciones internas basadas únicamente en
        evidencia local. La referencia CEFR describe el contenido; no certifica
        el nivel del estudiante.
      </p>

      <div className="skill-list">
        {skills.length > 0 ? (
          skills.map((skill) => {
            const state =
              skill.state && skill.state.evidence_count > 0
                ? skill.state
                : null;
            return (
              <article className="skill-card" key={skill.code}>
                <small>
                  {skillCategoryLabel[skill.category] ?? skill.category} ·
                  Referencia de contenido {skill.cefr_hint} · Dificultad{" "}
                  {skill.difficulty}/5 ·{" "}
                  {skill.is_active ? "Activa" : "Inactiva"}
                </small>
                <strong>{skill.name}</strong>
                <p>{skill.description}</p>

                {state ? (
                  <>
                    <dl className="skill-metrics">
                      <div>
                        <dt>Dominio estimado</dt>
                        <dd>{formatPercent(state.estimated_mastery)}</dd>
                      </div>
                      <div>
                        <dt>Confianza estimada</dt>
                        <dd>{formatPercent(state.confidence)}</dd>
                      </div>
                      <div>
                        <dt>Evidencias</dt>
                        <dd>{state.evidence_count}</dd>
                      </div>
                    </dl>
                    <p className="muted">
                      Próximo repaso:{" "}
                      {state.next_review_at
                        ? formatLocalDateTime(state.next_review_at)
                        : "sin fecha programada"}
                    </p>
                    {state.last_practised_at ? (
                      <p className="muted">
                        Última práctica evaluada:{" "}
                        {formatLocalDateTime(state.last_practised_at)}
                      </p>
                    ) : null}
                    {state.last_outcome ? (
                      <p className="muted">
                        Último resultado: {outcomeLabel[state.last_outcome]}
                      </p>
                    ) : null}
                    <p className="muted">
                      Criterio interno de dominio:{" "}
                      {state.internally_mastered
                        ? "alcanzado"
                        : "todavía no alcanzado"}
                    </p>
                  </>
                ) : (
                  <div className="skill-no-evidence">
                    <p>
                      Sin evidencia evaluada; DeutschOS no muestra porcentajes
                      sin datos.
                    </p>
                    <p className="muted">
                      Próximo repaso: sin fecha programada
                    </p>
                    <p className="muted">Evidencias: 0</p>
                  </div>
                )}

                {skill.unmet_prerequisites.length > 0 ? (
                  <p className="muted">
                    Prerrequisitos pendientes:{" "}
                    {skill.unmet_prerequisites
                      .map((code) => skillName(skills, code))
                      .join(", ")}
                  </p>
                ) : null}
              </article>
            );
          })
        ) : (
          <div className="empty compact">
            <p>No hay habilidades catalogadas en la base de datos.</p>
          </div>
        )}
      </div>
    </section>
  );
}
