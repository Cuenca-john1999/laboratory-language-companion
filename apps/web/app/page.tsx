import Link from "next/link";

import {
  getDashboard,
  getLearningReviews,
  getLearningSkills,
  getTodayPlan,
} from "../lib/api";
import { formatLocalDate } from "../lib/format";
import {
  intensityLabel,
  learningErrorMessage,
  skillName,
} from "../lib/learning";

export default async function DashboardPage() {
  const [dashboardResult, planResult, reviewsResult, skillsResult] =
    await Promise.allSettled([
      getDashboard(),
      getTodayPlan(),
      getLearningReviews(),
      getLearningSkills(),
    ]);

  const dashboard =
    dashboardResult.status === "fulfilled" ? dashboardResult.value : null;
  const dashboardError =
    dashboardResult.status === "rejected" ? dashboardResult.reason : null;
  const plan = planResult.status === "fulfilled" ? planResult.value : null;
  const planError = planResult.status === "rejected" ? planResult.reason : null;
  const reviews =
    reviewsResult.status === "fulfilled" ? reviewsResult.value : null;
  const skills = skillsResult.status === "fulfilled" ? skillsResult.value : [];
  const activeSkillCount = skills.filter((skill) => skill.is_active).length;
  const primarySkill = plan ? skillName(skills, plan.primary_skill_code) : null;

  return (
    <>
      <section className="hero">
        <div>
          <p className="eyebrow">
            {dashboard
              ? `GUTEN TAG, ${dashboard.preferred_name.toUpperCase()}`
              : "LLC LOCAL"}
          </p>
          <h1>
            Un poco de alemán.
            <br />
            <em>Todos los días.</em>
          </h1>
          {dashboard ? (
            <p className="lead">
              Tu objetivo personal: {dashboard.immediate_goal}
            </p>
          ) : (
            <p className="lead">
              El resumen del perfil no está disponible. Las demás áreas se
              muestran únicamente si respondieron con datos reales.
            </p>
          )}
        </div>

        {dashboard ? (
          <div className="status-card">
            <span
              aria-hidden="true"
              className={dashboard.lm_studio_available ? "dot online" : "dot"}
            />
            <div>
              <strong>
                {dashboard.lm_studio_available
                  ? "LM Studio conectado"
                  : "LM Studio desconectado"}
              </strong>
              <small>
                {dashboard.lm_studio_available
                  ? (dashboard.current_model ?? "Ningún modelo predeterminado")
                  : "El Learning Engine sigue disponible sin LM Studio"}
              </small>
            </div>
          </div>
        ) : (
          <div className="status-card">
            <span aria-hidden="true" className="dot" />
            <div>
              <strong>Estado del modelo no disponible</strong>
              <small>{learningErrorMessage(dashboardError)}</small>
            </div>
          </div>
        )}
      </section>

      <section className="actions">
        <Link className="primary" href="/progress">
          Abrir plan diario <span>→</span>
        </Link>
        <Link className="primary" href="/skills">
          Consultar habilidades <span>→</span>
        </Link>
        <Link className="primary secondary" href="/chat">
          Hablar con el profesor <span>→</span>
        </Link>
      </section>

      <section aria-labelledby="today-overview" className="daily-overview">
        <p className="eyebrow">PLAN DEL DÍA</p>
        {plan ? (
          <>
            <div className="section-heading">
              <div>
                <h2 id="today-overview">{plan.objective}</h2>
                <p className="muted">
                  Plan #{plan.id} · Currículo {plan.curriculum_version}
                </p>
              </div>
              <Link href="/progress">Ver bloques y ajustar el día →</Link>
            </div>
            <dl className="plan-facts dashboard-plan-facts">
              <div>
                <dt>Duración</dt>
                <dd>{plan.duration_minutes} min</dd>
              </div>
              <div>
                <dt>Intensidad</dt>
                <dd>{intensityLabel[plan.intensity]}</dd>
              </div>
              <div>
                <dt>Habilidad principal</dt>
                <dd>{primarySkill ?? "No asignada por el motor"}</dd>
                {skillsResult.status === "rejected" &&
                plan.primary_skill_code ? (
                  <small>Se muestra el código estable de la habilidad.</small>
                ) : null}
              </div>
              <div>
                <dt>Repasos pendientes</dt>
                <dd>{reviews ? reviews.total_due : "No disponible"}</dd>
                {reviewsResult.status === "rejected" ? (
                  <small>No se sustituyó por otro contador.</small>
                ) : null}
              </div>
            </dl>
          </>
        ) : (
          <div className="empty compact">
            <h2 id="today-overview">El plan diario no está disponible.</h2>
            <div className="error" role="alert">
              {learningErrorMessage(planError)}
            </div>
            <p>
              No mostramos un plan de ejemplo porque no sería una decisión real
              del Learning Engine.
            </p>
          </div>
        )}
      </section>

      <section className="grid dashboard-grid">
        <article>
          <p className="eyebrow">REVISIONES</p>
          {reviews ? (
            <>
              <strong className="metric">{reviews.total_due}</strong>
              <p>habilidades pendientes según el motor</p>
            </>
          ) : (
            <p className="muted">
              No se pudo consultar `/api/learning/reviews`.
            </p>
          )}
          <Link href="/progress">Ver plan →</Link>
        </article>

        <article className="wide">
          <p className="eyebrow">SESIONES RECIENTES</p>
          {dashboard ? (
            dashboard.recent_sessions.length ? (
              dashboard.recent_sessions.map((session) => (
                <div className="session" key={session.id}>
                  <span>{formatLocalDate(session.started_at)}</span>
                  <strong>
                    {session.session_type === "teacher_chat"
                      ? "Conversación con profesor"
                      : session.session_type}
                  </strong>
                  <small>{session.model_used ?? "Sin modelo asociado"}</small>
                </div>
              ))
            ) : (
              <p className="muted">No hay sesiones locales registradas.</p>
            )
          ) : (
            <p className="muted">No se pudo consultar el historial local.</p>
          )}
          <Link href="/sessions">Ver historial →</Link>
        </article>

        <article>
          <p className="eyebrow">HABILIDADES</p>
          {skillsResult.status === "fulfilled" ? (
            <>
              <strong className="metric">{activeSkillCount}</strong>
              <p>habilidades activas en el currículo</p>
            </>
          ) : (
            <p className="muted">No se pudo consultar el catálogo local.</p>
          )}
          <Link href="/skills">Ver evidencias →</Link>
        </article>
      </section>
    </>
  );
}
