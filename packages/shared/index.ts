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

export type TeacherRole = "teacher" | "deep_teacher";

export type TeacherRolesResponse = {
  provider: "lm_studio";
  available: boolean;
  roles: { role: TeacherRole; available: boolean }[];
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
  lm_studio_available: boolean;
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

export type StudyPracticalStatus =
  | "not_started"
  | "in_progress"
  | "viewed"
  | "needs_review"
  | "completed_by_user"
  | "paused";

export type StudyMissionType =
  | "automatic"
  | "standard"
  | "laboratory"
  | "frozen_city"
  | "underwater_exploration"
  | "space_mission"
  | "mixed";

export type StudyMission = {
  type: Exclude<StudyMissionType, "automatic" | "mixed">;
  label: string;
  concept: string;
  brief: string;
  example_de: string | null;
  example_es: string | null;
  objective: string;
  verifiable_task: string;
  original_content: boolean;
};

export type StudyWorkbookLink = {
  id: string;
  theory_source_id: string;
  theory_section_stable_key: string;
  workbook_source_id: string;
  workbook_source_version: number;
  workbook_pdf_page: number;
  printed_page_label: string | null;
  exercise_start: string | null;
  exercise_end: string | null;
  region: "full" | "left" | "right" | "both" | "unknown";
  comment: string | null;
  status: "candidate" | "user_confirmed" | "rejected" | "stale";
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type StudySection = {
  id: number;
  stable_key: string;
  order: number;
  title: string;
  topic: string | null;
  source_id: string;
  source_version: number;
  source_name: string;
  pdf_page_start: number | null;
  pdf_page_end: number | null;
  printed_page_label: string | null;
  editorial_status: string;
  practical_status: StudyPracticalStatus;
  current_pdf_page: number | null;
  selection_origin: string | null;
  last_activity_at: string | null;
  last_session_id: string | null;
  open_questions: number;
  workbook_link: StudyWorkbookLink | null;
  theme_number: number | null;
  title_es: string | null;
  title_de: string | null;
  printed_page_start: number | null;
  printed_page_end: number | null;
  printed_range_status: string;
  reference_pdf_page: number | null;
  manual_scan_layout: string | null;
  manual_region: string | null;
  outline: Array<{
    id: number;
    parent_id: number | null;
    hierarchy_level: string;
    local_number: string | null;
    title_es: string | null;
    title_de: string | null;
    printed_page: number | null;
    reference_pdf_page: number;
    visual_region: string;
    parse_status: string;
    manual_pdf_page: number | null;
    manual_scan_layout: string | null;
    manual_region: string | null;
    editorial_status: string;
    confidence: number;
  }>;
};

export type StudyPath = {
  source_id: string;
  source_version: number;
  source_name: string;
  workbook_source_id: string | null;
  workbook_source_name: string | null;
  sections: StudySection[];
};

export type StudySessionStatus =
  | "planned"
  | "active"
  | "paused"
  | "completed"
  | "abandoned";

export type StudySession = {
  id: string;
  kind: "guided" | "free";
  status: StudySessionStatus;
  source_id: string | null;
  source_version: number | null;
  source_name: string | null;
  section_id: number | null;
  section_stable_key: string | null;
  section_title: string;
  concept_name: string | null;
  pdf_page_start: number | null;
  pdf_page_end: number | null;
  current_pdf_page: number | null;
  printed_page_label: string | null;
  objective: string;
  mission: StudyMission;
  plan: {
    objective?: string;
    steps?: Array<{ id: string; label: string; completed: boolean }>;
  };
  checklist: unknown[];
  planned_minutes: number | null;
  active_seconds: number;
  started_at: string;
  paused_at: string | null;
  resumed_at: string | null;
  closed_at: string | null;
  subjective_result: string | null;
  final_pdf_page: number | null;
  final_workbook_exercise: string | null;
  next_action: string | null;
  created_at: string;
  updated_at: string;
};

export type StudyNote = {
  id: string;
  session_id: string | null;
  source_id: string | null;
  section_stable_key: string | null;
  concept_name: string | null;
  pdf_page: number | null;
  text: string;
  created_at: string;
  updated_at: string;
};

export type StudyQuestion = {
  id: string;
  session_id: string | null;
  source_id: string | null;
  section_stable_key: string | null;
  concept_name: string | null;
  pdf_page: number | null;
  question: string;
  answer_query_id: string | null;
  status: "open" | "clarified" | "revisit" | "archived";
  created_at: string;
  updated_at: string;
};

export type StudyPreferences = {
  mission_preference: StudyMissionType;
  active_source_id: string | null;
  active_section_stable_key: string | null;
};

export type StudyDashboard = {
  path_ready: boolean;
  total_sections: number;
  active_session: StudySession | null;
  recommendation: {
    kind: "active_session" | "paused_session" | "section" | "manual";
    reason: string;
    session_id: string | null;
    section_stable_key: string | null;
  };
  open_questions: number;
  recent_sessions: StudySession[];
  preferences: StudyPreferences;
};

export type StudySessionSummary = {
  id: string;
  status: StudySessionStatus;
  section_title: string;
  concept_name: string | null;
  mission_label: string | null;
  active_seconds: number;
  started_at: string;
  updated_at: string;
};

export type StudyMemory = {
  route_topics: number;
  started_topics: number;
  student_skills: number;
  skill_evidence: number;
  saved_notes: number;
  saved_questions: number;
  active_session_id: string | null;
  preferences_persisted: boolean;
  mission_preference: StudyMissionType | null;
};

export type StudyData = {
  total_sessions: number;
  sessions: StudySessionSummary[];
  memory: StudyMemory;
};

export type StudySessionDeleteResult = {
  deleted_sessions: number;
};

export type ChatHistoryMessage = {
  role: "user" | "assistant";
  content: string;
};

export type ChatRequest = {
  request_id: string;
  logical_generation_id: string;
  message: string;
  role: TeacherRole;
  history: ChatHistoryMessage[];
  session_id: number | null;
  continuation_from?: string;
  manual_continuation?: boolean;
  prior_segment_count?: number;
  automatic_continuation_count?: number;
  manual_continuation_count?: number;
};

export type ChatStreamEvent =
  | { type: "token"; content: string }
  | { type: "continuation"; active: boolean }
  | {
      type: "done";
      model: string;
      session_id: number;
      attempt_count: number;
      recovery: "empty_visible_content" | null;
      finish_reason: string | null;
      logical_generation_id: string;
      segment_count: number;
      automatic_continuation_count: number;
      manual_continuation_count: number;
      visible_character_count: number;
      continuation_available: boolean;
    }
  | { type: "error"; detail: string; retryable?: boolean };

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
  lm_studio_available: boolean;
  pdftoppm: boolean;
  tesseract: boolean;
  ocrmypdf: boolean;
  tesseract_languages: string[];
  vision_available: boolean;
  installed_models: string[];
};

export type SemanticIndexSummary = {
  model: string | null;
  model_digest: string | null;
  indexed: number;
  pending: number;
  failed: number;
  stale: number;
  excluded: number;
  dimension: number | null;
  normalization_version: string;
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
  semantic_index: SemanticIndexSummary;
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
  canonical_title: string | null;
  display_alias: string | null;
  author: string | null;
  publisher: string | null;
  edition: string | null;
  cefr_min: string | null;
  cefr_max: string | null;
  pedagogical_role:
    | "core_theory"
    | "core_workbook"
    | "core_answer_key"
    | "supplementary"
    | "reference"
    | "glossary"
    | "answer_key"
    | "unknown";
  source_priority: number;
  editorial_status:
    | "unreviewed"
    | "user_confirmed"
    | "system_suggested"
    | "rejected";
  user_selected_core: boolean;
  metadata_origin: string;
  metadata_confidence: number;
  editorial_notes: string | null;
  related_source_id: string | null;
  semantic_indexed_chunks: number;
  semantic_failed_chunks: number;
};

export type CoreSourceCandidate = {
  source: LibrarySource;
  suggested_role: LibrarySource["pedagogical_role"] | null;
  confidence: number;
  evidence: string[];
  unambiguous: boolean;
};

export type CoreSourcePair = {
  theory: LibrarySource | null;
  workbook: LibrarySource | null;
  answer_key: LibrarySource | null;
  candidates: CoreSourceCandidate[];
  ready: boolean;
};

export type EditorialSection = {
  id: number;
  source_version_id: number;
  stable_key: string;
  title: string;
  page_start: number;
  page_end: number;
  cefr_min: string | null;
  cefr_max: string | null;
  topic: string | null;
  content_role: string;
  derivation_method: string;
  provenance_confidence: number;
  editorial_status: string;
  notes: string | null;
  related_sections: number[];
};

export type PageQuality = {
  id: number;
  source_version_id: number;
  page_number: number;
  extraction_method: string;
  character_count: number;
  detected_language: string | null;
  text_density: number;
  replacement_ratio: number;
  weird_character_ratio: number;
  repeated_line_ratio: number;
  ordering_warning: boolean;
  columns_warning: boolean;
  tables_warning: boolean;
  damaged_german_ratio: number;
  quality: "good" | "acceptable" | "poor" | "unusable";
  warnings: string[];
  review_status: string;
  reviewed_variant_id: number | null;
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

export type LaboratorySummary = {
  catalogued_sources: number;
  recoverable_sources: number;
  sources_without_content: number;
  candidate_versions: number;
  needs_ocr_sources: number;
  error_sources: number;
  missing_files: number;
  pending_jobs: number;
};

export type DocumentState =
  | "unchanged"
  | "detected"
  | "candidate"
  | "pending_extraction"
  | "needs_ocr"
  | "processing"
  | "pending_validation"
  | "ready"
  | "active"
  | "historical"
  | "failed"
  | "missing"
  | "manual_review";

export type DocumentVersion = {
  id: number;
  source_id: string;
  version_number: number;
  content_hash: string;
  size_bytes: number;
  mtime_ns: number;
  observed_path: string | null;
  observed_name: string | null;
  detected_at: string;
  page_count: number | null;
  document_state: DocumentState;
  availability_state: string;
  extraction_state: string;
  chunk_state: string;
  embedding_state: string;
  activation_state: string;
  is_active: boolean;
  extractor: string | null;
  extractor_version: string | null;
  extraction_tool: string | null;
  extraction_tool_version: string | null;
  ocr_tool: string | null;
  ocr_tool_version: string | null;
  ocr_languages: string[];
  technical_metadata: Record<string, unknown>;
  provenance: string;
  change_reason: string | null;
  previous_version_id: number | null;
  error_code: string | null;
  error_detail: string | null;
  statistics: Record<string, unknown>;
  processed_at: string | null;
  chunks: number;
  embeddings: number;
};

export type LaboratorySource = {
  id: string;
  title: string;
  collection: string | null;
  format: string;
  current_path: string;
  source_status: string;
  document_state: DocumentState;
  needs_manual_review: boolean;
  active_version_id: number | null;
  active_version_number: number | null;
  latest_version_id: number | null;
  latest_version_number: number | null;
  page_count: number | null;
  active_chunks: number;
  active_embeddings: number;
  needs_ocr: boolean;
  error_code: string | null;
  change_pending: boolean;
};

export type LaboratorySourceDetail = LaboratorySource & {
  canonical_title: string | null;
  display_alias: string | null;
  author: string | null;
  publisher: string | null;
  first_seen_at: string;
  last_seen_at: string;
  versions: DocumentVersion[];
};

export type InventoryChange = {
  outcome:
    | "unchanged"
    | "modified"
    | "new"
    | "renamed"
    | "missing"
    | "duplicate"
    | "manual_review";
  source_id: string;
  relative_path: string;
  title: string;
  previous_hash: string | null;
  current_hash: string | null;
  active_version_id: number | null;
  candidate_version_id: number | null;
  message: string;
};

export type DocumentInventoryResult = {
  job_id: string;
  generated_at: string;
  files_scanned: number;
  unchanged: number;
  modified: number;
  new: number;
  renamed: number;
  missing: number;
  duplicates: number;
  manual_review: number;
  candidate_versions_created: number;
  changes: InventoryChange[];
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
  pedagogical_role: LibrarySource["pedagogical_role"];
  source_priority: number;
  extraction_quality: number;
  page_quality: string | null;
  retrieval_origins: string[];
};

export type LibrarySearchResponse = {
  query: string;
  requested_mode: "lexical" | "semantic" | "hybrid";
  effective_mode: "lexical" | "semantic" | "hybrid";
  semantic_available: boolean;
  results: LibrarySearchResult[];
  warning: string | null;
  core_results: number;
  supplementary_results: number;
  query_embedding_cache_hit: boolean;
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

export type TeacherEvidenceConfidence =
  | "solid"
  | "moderate"
  | "limited"
  | "insufficient";

export type TeacherExample = {
  german: string;
  spanish: string | null;
  note: string | null;
};

export type TeacherPublicAnswer = {
  direct_answer: string;
  key_points: string[];
  examples: TeacherExample[];
  important_nuance: string | null;
  ambiguity_note: string | null;
  follow_up_question: string | null;
};

export type TeacherSource = {
  citation: string;
  source_id: string;
  source_name: string;
  page_start: number | null;
  page_end: number | null;
  start_seconds: number | null;
  end_seconds: number | null;
  section: string | null;
  snippet: string;
  review_status: string;
  rights: string;
  extraction_quality: number;
  content_role: string;
  retrieval_score: number;
  pedagogical_role: LibrarySource["pedagogical_role"];
  evidence_origin: "core" | "supplementary";
  page_quality: string | null;
  location_id: string | null;
  public_location: string | null;
  memory_status: PedagogicalMemoryStatus | null;
  printed_page_label: string | null;
  scan_layout: ScanLayout;
  region: EvidenceRegion;
};

export type TeacherTimings = {
  planning_ms: number;
  retrieval_ms: number;
  generation_ms: number;
  validation_ms: number;
  total_ms: number;
  embedding_ms: number;
  model_selection_ms: number;
  fts_ms: number;
  vector_ms: number;
  ranking_ms: number;
  repair_ms: number;
  intent_detection_ms: number;
  memory_lookup_ms: number;
  hybrid_fallback_ms: number;
};

export type SourceLookupLocation = {
  location_id: string;
  source_id: string;
  source_name: string;
  source_role: LibrarySource["pedagogical_role"];
  source_version: number;
  pdf_page: number | null;
  printed_page: string | null;
  scan_layout: ScanLayout;
  region: EvidenceRegion;
  heading: string | null;
  review_status: PedagogicalMemoryStatus;
  citation: string;
  snippet: string;
  provenance: "memory" | "hybrid";
};

export type SourceLookup = {
  status:
    | "verified_location"
    | "candidate_locations"
    | "multiple_locations"
    | "conflict"
    | "stale"
    | "no_location"
    | "retrieval_error";
  concept: {
    concept_id: string | null;
    canonical_name: string;
    display_name_es: string | null;
    display_name_de: string | null;
  } | null;
  summary: string;
  locations: SourceLookupLocation[];
  available_location_count: number;
  warnings: string[];
  memory_hit: boolean;
  lookup_cache_hit: boolean;
  hybrid_fallback: boolean;
  used_generation: boolean;
};

export type TeacherQuery = {
  query_id: string;
  conversation_id: string;
  parent_query_id: string | null;
  question: string;
  status: "completed" | "insufficient" | "failed" | "cancelled" | "timed_out";
  answer: TeacherPublicAnswer;
  confidence: TeacherEvidenceConfidence;
  sources: TeacherSource[];
  warnings: string[];
  retrieval_mode: "lexical" | "semantic" | "hybrid";
  semantic_search_available: boolean;
  timings: TeacherTimings;
  created_at: string;
  failure_reason:
    | "no_evidence"
    | "weak_evidence"
    | "retrieval_failure"
    | "model_unavailable"
    | "generation_failure"
    | "citation_validation_failure"
    | "repair_failure"
    | "cancelled"
    | "timeout"
    | null;
  models: Record<string, string>;
  cache_hit: boolean;
  answer_verified: boolean;
  memory_used: boolean;
  response_feedback: MemoryFeedbackVerdict | null;
  answer_kind:
    | "teacher_answer"
    | "source_lookup"
    | "teacher_answer_with_source_lookup";
  source_lookup: SourceLookup | null;
  used_generation: boolean;
  memory_hit: boolean;
  lookup_cache_hit: boolean;
  hybrid_fallback: boolean;
  answer_cache_hit: boolean;
};

export type LibraryModelRouting = {
  lm_studio_available: boolean;
  installed_models: string[];
  roles: {
    role: "planner" | "embedding" | "teacher" | "deep" | "vision" | "repair";
    configured_model: string;
    available: boolean;
    selected_model: string | null;
    fallback_models: string[];
  }[];
  policy_version: string;
};

export type TeacherStreamEvent = {
  event:
    | "accepted"
    | "planning"
    | "retrieving"
    | "generating"
    | "provisional"
    | "verified"
    | "error";
  message: string;
  query: TeacherQuery | null;
  failure_reason: TeacherQuery["failure_reason"];
};

export type TeacherConversationSummary = {
  conversation_id: string;
  latest_query_id: string;
  question: string;
  answer_excerpt: string;
  confidence: TeacherEvidenceConfidence;
  source_count: number;
  turn_count: number;
  updated_at: string;
};

export type PedagogicalMemoryStatus =
  | "candidate"
  | "system_verified"
  | "user_confirmed"
  | "rejected"
  | "conflict"
  | "stale";

export type MemoryFeedbackVerdict = "correct" | "incorrect" | "unknown";
export type ScanLayout = "single_page" | "double_page" | "mixed" | "unknown";
export type EvidenceRegion =
  | "full"
  | "left"
  | "right"
  | "both"
  | "custom"
  | "unknown";

export type ConceptAlias = {
  id: number;
  text: string;
  language: string;
  normalized_text: string;
  origin: string;
  status: PedagogicalMemoryStatus;
  confidence: "low" | "moderate" | "high";
  user_confirmed: boolean;
};

export type EvidenceLocation = {
  id: string;
  concept_id: string;
  concept_name: string;
  source_id: string;
  source_name: string;
  source_version: number;
  chunk_id: number | null;
  pdf_page_number: number | null;
  printed_page_label: string | null;
  scan_layout: ScanLayout;
  region: EvidenceRegion;
  heading: string | null;
  evidence_snippet: string;
  extraction_quality: number;
  status: PedagogicalMemoryStatus;
  origin: string;
  public_citation: string;
  reviewed_at: string | null;
};

export type PedagogicalConceptSummary = {
  id: string;
  canonical_name: string;
  language: string;
  display_name_es: string | null;
  display_name_de: string | null;
  category: string;
  description: string | null;
  status: PedagogicalMemoryStatus;
  aliases: ConceptAlias[];
  location_counts: Record<string, number>;
  updated_at: string;
};

export type PedagogicalConcept = PedagogicalConceptSummary & {
  relations: {
    id: number;
    source_concept_id: string;
    target_concept_id: string;
    target_name: string;
    relation_type: string;
    status: PedagogicalMemoryStatus;
    origin: string;
  }[];
  locations: EvidenceLocation[];
  query_ids: string[];
};

export type PedagogicalMemorySummary = {
  concepts: number;
  aliases: number;
  locations: number;
  relations: number;
  by_status: Record<string, number>;
  pending_review: number;
};

export type MemoryReviewQueueItem = {
  target_type: "concept" | "location" | "relation";
  target_id: string;
  title: string;
  subtitle: string | null;
  status: PedagogicalMemoryStatus;
  priority: number;
  used_by_queries: number;
  source_role: LibrarySource["pedagogical_role"] | null;
  last_used_at: string | null;
};

export type MemoryAudit = {
  id: number;
  operation_id: string;
  actor: string;
  action: string;
  target_type: string;
  target_id: string;
  query_id: string | null;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  comment: string | null;
  created_at: string;
};
