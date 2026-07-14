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

export type LibraryCapabilities = {
  fts5: boolean;
  pdftotext: boolean;
  ffprobe: boolean;
  transcription_backend: string | null;
  transcription_model: string | null;
  semantic_available: boolean;
  embedding_provider: string | null;
  embedding_model: string | null;
  ollama_available: boolean;
};

export type InventoryExtension = {
  extension: string;
  files: number;
  bytes: number;
};

export type InventoryReport = {
  generated_at: string;
  root: string;
  files: number;
  directories: number;
  total_bytes: number;
  by_extension: InventoryExtension[];
  without_extension: number;
  hidden_files: number;
  empty_files: number;
  over_50_mb: number;
  over_250_mb: number;
  over_1_gb: number;
  possible_duplicate_groups: number;
  confirmed_duplicate_groups: number;
  confirmed_duplicate_files: number;
  symlinks: number;
  unknown_formats: number;
  documents: number;
  subtitles: number;
  audio: number;
  video: number;
  images: number;
  archives: number;
  problematic_names: string[];
  overly_long_paths: string[];
  inaccessible: string[];
  duplicate_groups: string[][];
};

export type LibrarySummary = {
  materials_root: string;
  runtime_root: string;
  schema_version: number;
  total_sources: number;
  total_bytes: number;
  sources_present: number;
  sources_missing: number;
  processed: number;
  pending: number;
  errors: number;
  unsupported: number;
  needs_ocr: number;
  needs_transcription: number;
  chunks: number;
  embeddings: number;
  knowledge_units: number;
  knowledge_by_status: Record<string, number>;
  jobs_by_status: Record<string, number>;
  capabilities: LibraryCapabilities;
  latest_inventory: InventoryReport | null;
};

export type LibrarySource = {
  id: string;
  current_path: string;
  name: string;
  kind: string;
  format: string;
  size_bytes: number;
  content_hash: string | null;
  language: string | null;
  cefr_level: string | null;
  topics: string[];
  provenance: string | null;
  rights: string;
  priority: number;
  editorial_confidence: number;
  review_status: string;
  status: string;
  processing_state: string;
  duplicate_of_source_id: string | null;
  first_seen_at: string;
  last_seen_at: string;
  current_version: number;
};

export type LibrarySourceVersion = {
  id: number;
  source_id: string;
  version_number: number;
  content_hash: string;
  size_bytes: number;
  extractor: string | null;
  extractor_version: string | null;
  processing_state: string;
  error_code: string | null;
  statistics: Record<string, unknown>;
  created_at: string;
};

export type LibraryChunk = {
  id: number;
  source_id: string;
  source_version_id: number;
  source_name: string;
  source_path: string;
  text: string;
  title: string | null;
  page_start: number | null;
  page_end: number | null;
  start_seconds: number | null;
  end_seconds: number | null;
  level: string | null;
  topics: string[];
  review_status: string;
  rights: string;
  source_version: number;
  content_role: string;
};

export type LibrarySearchResult = Omit<LibraryChunk, "text"> & {
  lexical_score: number | null;
  semantic_score: number | null;
  combined_score: number;
  snippet: string;
};

export type LibrarySearchResponse = {
  query: string;
  requested_mode: "lexical" | "semantic" | "hybrid";
  effective_mode: "lexical" | "semantic" | "hybrid";
  semantic_available: boolean;
  results: LibrarySearchResult[];
  warning: string | null;
};

export type LibraryJob = {
  id: string;
  kind: string;
  state: string;
  priority: number;
  progress_current: number;
  progress_total: number;
  attempts: number;
  cursor: string | null;
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  cancel_requested: boolean;
};

export type KnowledgeCitation = { chunk_id: number; quote: string };

export type KnowledgeUnit = {
  id: string;
  kind: string;
  title: string;
  content_es: string;
  german_examples: string[];
  translations: string[];
  cefr_level: string;
  topics: string[];
  keywords: string[];
  warnings: string[];
  citations: KnowledgeCitation[];
  confidence: number;
  status: string;
  model: string;
  prompt_version: string;
  stale: boolean;
  created_at: string;
  updated_at: string;
};

export type GroundedDraft = {
  id: string;
  query: string;
  objective: string;
  payload: {
    title: string;
    explanation: string;
    examples: string[];
    exercises: string[];
    claims: { text: string; source_chunk_ids: number[] }[];
    warnings: string[];
    confidence: number;
  };
  sources: {
    chunk_id: number;
    source_id: string;
    source_name: string;
    page_start: number | null;
    page_end: number | null;
    start_seconds: number | null;
    end_seconds: number | null;
    source_version: number;
    review_status: string;
  }[];
  model: string;
  prompt_version: string;
  review_status: "draft";
  evidence_sufficient: boolean;
  created_at: string;
};
