import { LearningSkills } from "../../components/LearningSkills";
import { getLearningSkills } from "../../lib/api";
import { learningErrorMessage } from "../../lib/learning";

export default async function SkillsPage() {
  const result = await Promise.allSettled([getLearningSkills()]);
  const skillsResult = result[0];

  return (
    <>
      <section>
        <p className="eyebrow">PROGRESO INTERNO</p>
        <h1>Habilidades y evidencias.</h1>
        <p className="lead">
          Consulta las estimaciones internas que usa DeutschOS para planificar.
          No representan una certificación ni un nivel CEFR global.
        </p>
      </section>

      {skillsResult.status === "fulfilled" ? (
        <LearningSkills skills={skillsResult.value} />
      ) : (
        <section aria-labelledby="skills-error" className="empty compact">
          <h2 id="skills-error">No se pudieron consultar las habilidades.</h2>
          <div className="error" role="alert">
            {learningErrorMessage(skillsResult.reason)}
          </div>
          <p>No mostraremos estimaciones sin datos de la API local.</p>
        </section>
      )}
    </>
  );
}
