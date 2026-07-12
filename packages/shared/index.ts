export type ModelInfo = {
  name: string;
  size: number | null;
  modified_at: string | null;
};

export type ModelsResponse = {
  provider: string;
  available: boolean;
  models: ModelInfo[];
  error: string | null;
};

export type Session = {
  id: number;
  session_type: string;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  model_used: string | null;
  summary: string;
};

export type Dashboard = {
  preferred_name: string;
  immediate_goal: string;
  current_model: string | null;
  ollama_available: boolean;
  pending_reviews: number;
  recent_sessions: Session[];
};

export type ProfileUpdate = {
  preferred_name: string;
  native_language: string;
  additional_languages: string[];
  current_location: string;
  professional_background: string;
  learning_goals: string[];
  interests: string[];
  learning_preferences: Record<string, unknown>;
};

export type Profile = ProfileUpdate & {
  id: number;
  created_at: string;
  updated_at: string;
};

export type Mistake = {
  id: number;
  original_text: string;
  corrected_text: string;
  explanation_es: string;
  category: string;
  severity: string;
  status: string;
  next_review_at: string | null;
};

export type ChatHistoryMessage = {
  role: "user" | "assistant";
  content: string;
};

export type ChatRequest = {
  message: string;
  model: string;
  history: ChatHistoryMessage[];
  session_id: number | null;
};

export type ChatStreamEvent =
  | { type: "token"; content: string }
  | { type: "done"; model: string; session_id: number }
  | { type: "error"; detail: string };

export type CefrHint = "pre-A1" | "A1";

export type LearningExerciseType = string;

export type MasteryCriteria = {
  min_mastery: number;
  min_confidence: number;
  min_evidence: number;
  min_unassisted_streak: number;
};

export type CurriculumSkill = {
  code: string;
  name: string;
  category: string;
  description: string;
  cefr_hint: CefrHint;
  order: number;
  difficulty: number;
  prerequisite_codes: string[];
  compatible_exercise_types: LearningExerciseType[];
  mastery_criteria: MasteryCriteria;
  curriculum_version: string;
  is_active: boolean;
};

export type CurriculumResponse = {
  version: string;
  skills: CurriculumSkill[];
};

export type AttemptOutcome =
  | "failure"
  | "partial"
  | "correct_with_help"
  | "correct_without_help";

export type StudentSkillState = {
  skill_code: string;
  estimated_mastery: number;
  confidence: number;
  evidence_count: number;
  last_practised_at: string | null;
  next_review_at: string | null;
  last_outcome: AttemptOutcome | null;
  unassisted_streak: number;
  internally_mastered: boolean;
  mastered_at: string | null;
};

export type LearningSkill = CurriculumSkill & {
  state: StudentSkillState | null;
  eligible_for_introduction: boolean;
  unmet_prerequisites: string[];
};

export type PlanIntensity = "low" | "normal" | "high";

export type LearningModality = "reading" | "writing" | "listening" | "speaking";

export type DailyPlanBlock = {
  position: number;
  kind: "review" | "new_skill" | "practice";
  skill_code: string;
  exercise_type: LearningExerciseType;
  duration_minutes: number;
  objective: string;
  internal_reason: string;
};

export type DailyPlan = {
  id: number;
  plan_date: string;
  engine_version: string;
  curriculum_version: string;
  generated_at: string;
  objective: string;
  requested_minutes: number;
  duration_minutes: number;
  motivation: number;
  intensity: PlanIntensity;
  primary_skill_code: string;
  review_skill_codes: string[];
  new_skill_code: string | null;
  blocks: DailyPlanBlock[];
  internal_reason: string;
  reason_codes: string[];
};

export type DailyPlanRequest = {
  available_minutes: number;
  motivation: number;
};

export type LearningReview = {
  skill_code: string;
  skill_name: string;
  due_at: string;
  overdue: boolean;
  estimated_mastery: number;
  confidence: number;
  evidence_count: number;
};

export type LearningReviewsResponse = {
  as_of: string;
  total_due: number;
  items: LearningReview[];
};

export type LearningAttemptCreate = {
  submission_id: string;
  skill_code: string;
  source: "manual_assessment" | "diagnostic";
  exercise_type: LearningExerciseType;
  prompt: string;
  student_answer: string;
  expected_answer: string | null;
  outcome: AttemptOutcome;
  feedback: string;
  corrects_submission_id: string | null;
};

export type LearningAttemptReceipt = {
  created: boolean;
  evidence_id: number;
  exercise_attempt_id: number;
  learning_session_id: number;
  engine_version: string;
  outcome: AttemptOutcome;
  derived_score: number;
  corrected_evidence_id: number | null;
  state: StudentSkillState;
};
