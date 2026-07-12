import type { DailyPlan, LearningSkill } from "@deutschos/shared";

import { formatCivilDate, formatLocalDateTime } from "../lib/format";
import { blockKindLabel, intensityLabel, skillName } from "../lib/learning";

type LearningPlanViewProps = {
  plan: DailyPlan;
  skills: LearningSkill[];
  pendingReviewCount?: number;
};

export function LearningPlanView({
  plan,
  skills,
  pendingReviewCount,
}: LearningPlanViewProps) {
  const primarySkill = skillName(skills, plan.primary_skill_code);
  const newSkill = skillName(skills, plan.new_skill_code);

  return (
    <section aria-labelledby="daily-plan" className="plan-view">
      <div className="section-heading">
        <div>
          <p className="eyebrow">PLAN DEL {formatCivilDate(plan.plan_date)}</p>
          <h2 id="daily-plan">{plan.objective}</h2>
        </div>
        <p className="muted">
          Generado {formatLocalDateTime(plan.generated_at)} · Motor{" "}
          {plan.engine_version}
        </p>
      </div>

      <dl className="plan-facts">
        <div>
          <dt>Duración</dt>
          <dd>{plan.duration_minutes} min</dd>
          {plan.duration_minutes !== plan.requested_minutes ? (
            <small>{plan.requested_minutes} min disponibles</small>
          ) : null}
        </div>
        <div>
          <dt>Intensidad</dt>
          <dd>{intensityLabel[plan.intensity]}</dd>
          <small>Motivación indicada: {plan.motivation}/5</small>
        </div>
        <div>
          <dt>Habilidad principal</dt>
          <dd>{primarySkill ?? "No asignada por el motor"}</dd>
        </div>
        <div>
          <dt>Repasos pendientes</dt>
          <dd>
            {pendingReviewCount === undefined
              ? "No disponible"
              : pendingReviewCount}
          </dd>
        </div>
      </dl>

      <div className="plan-selection">
        <p>
          <strong>Habilidades de repaso:</strong>{" "}
          {plan.review_skill_codes.length > 0
            ? plan.review_skill_codes
                .map((code) => skillName(skills, code))
                .join(", ")
            : "Ninguna en este plan"}
        </p>
        <p>
          <strong>Habilidad nueva:</strong> {newSkill ?? "Ninguna en este plan"}
        </p>
      </div>

      <h2>Bloques</h2>
      {plan.blocks.length > 0 ? (
        <ol className="plan-blocks">
          {plan.blocks.map((block) => (
            <li key={`${block.position}-${block.skill_code}`}>
              <article>
                <small>
                  Bloque {block.position} · {blockKindLabel[block.kind]} ·{" "}
                  {block.duration_minutes} min
                </small>
                <strong>{skillName(skills, block.skill_code)}</strong>
                <p>{block.objective}</p>
                <p className="muted">
                  Tipo de ejercicio: {block.exercise_type}
                </p>
                <details>
                  <summary>Motivo interno del bloque</summary>
                  <p>{block.internal_reason}</p>
                </details>
              </article>
            </li>
          ))}
        </ol>
      ) : (
        <div className="empty compact">
          <p>El motor no ha programado bloques para este plan.</p>
        </div>
      )}

      <details className="plan-reason">
        <summary>Decisión auditable del planificador</summary>
        <p>{plan.internal_reason}</p>
        {plan.reason_codes.length > 0 ? (
          <p className="muted">
            Códigos: <code>{plan.reason_codes.join(", ")}</code>
          </p>
        ) : null}
      </details>
    </section>
  );
}
