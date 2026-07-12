import { DailyPlanBuilder } from "../../components/DailyPlanBuilder";
import {
  getLearningReviews,
  getLearningSkills,
  getTodayPlan,
} from "../../lib/api";
import { learningErrorMessage } from "../../lib/learning";

export default async function ProgressPage() {
  const [planResult, reviewsResult, skillsResult] = await Promise.allSettled([
    getTodayPlan(),
    getLearningReviews(),
    getLearningSkills(),
  ]);

  return (
    <>
      <section>
        <p className="eyebrow">LEARNING ENGINE</p>
        <h1>Tu plan de hoy.</h1>
        <p className="lead">
          Un plan local y determinista, calculado a partir de tus evidencias,
          repasos, tiempo disponible y motivación.
        </p>
      </section>

      {planResult.status === "rejected" ? (
        <div className="warning" role="status">
          No se recuperó un plan guardado para hoy:{" "}
          {learningErrorMessage(planResult.reason)}. Puedes solicitar uno con el
          formulario; no se muestran datos de ejemplo.
        </div>
      ) : null}
      {reviewsResult.status === "rejected" ? (
        <div className="warning" role="status">
          No se pudo consultar el total de repasos:{" "}
          {learningErrorMessage(reviewsResult.reason)}
        </div>
      ) : null}
      {skillsResult.status === "rejected" ? (
        <div className="warning" role="status">
          No se pudieron resolver los nombres del catálogo:{" "}
          {learningErrorMessage(skillsResult.reason)}
        </div>
      ) : null}
      <DailyPlanBuilder
        initialPlan={
          planResult.status === "fulfilled" ? planResult.value : null
        }
        pendingReviewCount={
          reviewsResult.status === "fulfilled"
            ? reviewsResult.value.total_due
            : undefined
        }
        skills={skillsResult.status === "fulfilled" ? skillsResult.value : []}
      />
    </>
  );
}
