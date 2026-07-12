import type {
  AttemptOutcome,
  DailyPlanBlock,
  LearningSkill,
  PlanIntensity,
} from "@deutschos/shared";

export const intensityLabel: Record<PlanIntensity, string> = {
  low: "Baja",
  normal: "Normal",
  high: "Alta",
};

export const blockKindLabel: Record<DailyPlanBlock["kind"], string> = {
  review: "Repaso",
  new_skill: "Habilidad nueva",
  practice: "Práctica",
};

export const outcomeLabel: Record<AttemptOutcome, string> = {
  failure: "Fallo",
  partial: "Parcial",
  correct_with_help: "Correcto con ayuda",
  correct_without_help: "Correcto sin ayuda",
};

export const skillCategoryLabel: Record<string, string> = {
  grammar: "Gramática",
  vocabulary: "Vocabulario",
  reading: "Lectura",
  listening: "Comprensión auditiva",
  writing: "Escritura",
  speaking: "Expresión oral",
  pronunciation: "Pronunciación",
  professional_language: "Alemán profesional",
};

export function learningErrorMessage(reason: unknown): string {
  return reason instanceof Error
    ? reason.message
    : "No se pudo consultar la API local.";
}

export function skillName(
  skills: LearningSkill[],
  code: string | null,
): string | null {
  if (code === null) {
    return null;
  }
  return skills.find((skill) => skill.code === code)?.name ?? code;
}
