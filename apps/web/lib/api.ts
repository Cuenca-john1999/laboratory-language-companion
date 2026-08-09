import type {
  AuditDecisionSet,
  AuditExport,
  AuditExportPreview,
  AuditExportRequest,
  AuditImport,
  AuditValidation,
  CurriculumResponse,
  ComparisonEvent,
  ConsolidatedNode,
  ConsolidatedTopic,
  CoreSourcePair,
  DailyPlan,
  DailyPlanRequest,
  Dashboard,
  DocumentInventoryResult,
  DocumentReviewBatch,
  DocumentReviewDecision,
  DocumentReviewDetail,
  DocumentReviewQueue,
  DocumentReviewSummary,
  DocumentRun,
  DocumentRunDetail,
  DocumentRunPageList,
  DocumentPageBlock,
  DocumentVersion,
  LearningAttemptCreate,
  LearningAttemptReceipt,
  LearningReviewsResponse,
  LearningSkill,
  GroundedDraft,
  EvidenceRegion,
  ExerciseSolutionRelation,
  KnowledgeUnit,
  LibraryChunk,
  LibraryJob,
  LaboratorySource,
  LaboratorySourceDetail,
  LaboratorySummary,
  LibrarySearchResponse,
  LibrarySource,
  LibrarySourceVersion,
  LibrarySummary,
  LibraryModelRouting,
  EditorialSection,
  MemoryAudit,
  MemoryFeedbackVerdict,
  MemoryReviewQueueItem,
  PageQuality,
  PageCorrespondence,
  PageCorrespondenceList,
  PedagogicalConcept,
  PedagogicalConceptSummary,
  PedagogicalMemorySummary,
  Mistake,
  ModelsResponse,
  Profile,
  ProposedHierarchyNode,
  Session,
  ScanLayout,
  TeacherConversationSummary,
  TeacherQuery,
  TeacherStreamEvent,
  TeacherRolesResponse,
  StudyDashboard,
  StudyData,
  StudyMissionType,
  StudyNote,
  StudyPath,
  StudyPreferences,
  StudyQuestion,
  StudySession,
  StudySessionDeleteResult,
  StudySessionStatus,
  StudyWorkbookLink,
  StructureCandidate,
  StructureCandidateList,
  VersionComparison,
  ClosureSnapshot,
  ClosureStatus,
} from "@deutschos/shared";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function validationItemMessage(value: unknown): string | null {
  if (!isRecord(value) || typeof value.msg !== "string") {
    return null;
  }

  const location = Array.isArray(value.loc)
    ? value.loc
        .filter((part) => part !== "body")
        .map(String)
        .join(".")
    : "";

  return location ? `${location}: ${value.msg}` : value.msg;
}

function detailMessage(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }

  if (Array.isArray(detail)) {
    const messages = detail
      .map(validationItemMessage)
      .filter((message): message is string => message !== null);
    return messages.length > 0 ? messages.join(" · ") : null;
  }

  if (isRecord(detail) && typeof detail.message === "string") {
    return detail.message;
  }

  return null;
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      cache: "no-store",
      headers,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") {
      throw cause;
    }
    throw new Error(
      "No se pudo conectar con la API local. Comprueba que esté iniciada.",
      { cause },
    );
  }

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = isRecord(body) ? body.detail : null;
    throw new Error(
      detailMessage(detail) ?? `La API respondió con ${response.status}`,
    );
  }

  return response;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init);
  return response.json() as Promise<T>;
}

export async function apiNoContent(
  path: string,
  init?: RequestInit,
): Promise<void> {
  await request(path, init);
}

export const operationId = (scope: string) => `${scope}-${crypto.randomUUID()}`;

export const getStudyDashboard = () =>
  api<StudyDashboard>("/api/study/dashboard");
export const getStudyPath = (query = "") =>
  api<StudyPath>(
    `/api/study/path${query ? `?query=${encodeURIComponent(query)}` : ""}`,
  );
export const reviewCanonicalStudyTopic = (
  themeNumber: number,
  payload: Record<string, unknown>,
) =>
  api<unknown>(`/api/library/canonical-route/topics/${themeNumber}/review`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const getStudyHistory = (
  status?: StudySessionStatus,
  missionType?: StudyMissionType,
) => {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (missionType) params.set("mission_type", missionType);
  return api<StudySession[]>(`/api/study/history?${params}`);
};
export const getStudyData = () => api<StudyData>("/api/study/data");
export const startStudySession = (payload: Record<string, unknown>) =>
  api<StudySession>("/api/study/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const transitionStudySession = (
  sessionId: string,
  payload: Record<string, unknown>,
) =>
  api<StudySession>(`/api/study/sessions/${sessionId}/transition`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const updateStudyPosition = (
  sessionId: string,
  payload: Record<string, unknown>,
) =>
  api<StudySession>(`/api/study/sessions/${sessionId}/position`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const getStudyNotes = (sessionId?: string) =>
  api<StudyNote[]>(
    `/api/study/notes${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`,
  );
export const createStudyNote = (payload: Record<string, unknown>) =>
  api<StudyNote>("/api/study/notes", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const updateStudyNote = (
  noteId: string,
  payload: Record<string, unknown>,
) =>
  api<StudyNote>(`/api/study/notes/${noteId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const deleteStudyNote = (noteId: string) =>
  apiNoContent(`/api/study/notes/${noteId}`, {
    method: "DELETE",
    body: JSON.stringify({
      operation_id: operationId("delete-note"),
      confirmation: "BORRAR",
    }),
  });
export const getStudyQuestions = (sessionId?: string) =>
  api<StudyQuestion[]>(
    `/api/study/questions${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`,
  );
export const createStudyQuestion = (payload: Record<string, unknown>) =>
  api<StudyQuestion>("/api/study/questions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const updateStudyQuestion = (
  questionId: string,
  payload: Record<string, unknown>,
) =>
  api<StudyQuestion>(`/api/study/questions/${questionId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const deleteStudyQuestion = (questionId: string) =>
  apiNoContent(`/api/study/questions/${questionId}`, {
    method: "DELETE",
    body: JSON.stringify({
      operation_id: operationId("delete-question"),
      confirmation: "BORRAR",
    }),
  });
export const createStudyWorkbookLink = (payload: Record<string, unknown>) =>
  api<StudyWorkbookLink>("/api/study/workbook-links", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const updateStudyWorkbookLink = (
  linkId: string,
  payload: Record<string, unknown>,
) =>
  api<StudyWorkbookLink>(`/api/study/workbook-links/${linkId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const reviewStudyWorkbookLink = (
  linkId: string,
  action: "confirm" | "reject" | "unknown" | "revert",
) =>
  api<StudyWorkbookLink>(`/api/study/workbook-links/${linkId}/review`, {
    method: "POST",
    body: JSON.stringify({
      operation_id: operationId(`workbook-${action}`),
      action,
    }),
  });
export const deleteStudySession = (sessionId: string) =>
  api<StudySessionDeleteResult>(`/api/study/sessions/${sessionId}`, {
    method: "DELETE",
    body: JSON.stringify({
      operation_id: operationId("delete-session"),
      confirmation: "BORRAR",
    }),
  });
export const deleteStudySessions = (sessionIds: string[]) =>
  api<StudySessionDeleteResult>("/api/study/sessions", {
    method: "DELETE",
    body: JSON.stringify({
      operation_id: operationId("delete-study-sessions"),
      confirmation: "BORRAR",
      session_ids: sessionIds,
    }),
  });
export const clearStudySessions = () =>
  api<StudySessionDeleteResult>("/api/study/sessions/all", {
    method: "DELETE",
    body: JSON.stringify({
      operation_id: operationId("clear-study-sessions"),
      confirmation: "VACIAR HISTORIAL",
    }),
  });
export const updateStudyPreferences = (mission: StudyMissionType) =>
  api<StudyPreferences>("/api/study/preferences", {
    method: "PUT",
    body: JSON.stringify({
      operation_id: operationId("study-preferences"),
      mission_preference: mission,
    }),
  });
export const askStudyTeacher = (
  sessionId: string,
  payload: Record<string, unknown>,
) =>
  api<TeacherQuery>(`/api/study/sessions/${sessionId}/teacher`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

function parseNdjsonLine<T>(line: string): T {
  try {
    return JSON.parse(line) as T;
  } catch (cause) {
    throw new Error("La API devolvió un evento de streaming no válido.", {
      cause,
    });
  }
}

export async function* streamNdjson<T>(
  path: string,
  init?: RequestInit,
): AsyncGenerator<T> {
  const response = await request(path, init);
  if (!response.body) {
    throw new Error("La API no devolvió un flujo de respuesta.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });

    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.trim()) {
        yield parseNdjsonLine<T>(line);
      }
    }

    if (done) {
      break;
    }
  }

  if (buffer.trim()) {
    yield parseNdjsonLine<T>(buffer);
  }
}

export async function* streamSse<T>(
  path: string,
  init?: RequestInit,
): AsyncGenerator<T> {
  const response = await request(path, init);
  if (!response.body) {
    throw new Error("La API no devolvió un flujo de respuesta.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const data = event
        .split("\n")
        .find((line) => line.startsWith("data: "))
        ?.slice(6);
      if (data) {
        yield parseNdjsonLine<T>(data);
      }
    }
    if (done) break;
  }
}

export const getDashboard = () => api<Dashboard>("/api/dashboard");
export const getModels = () => api<ModelsResponse>("/api/models");
export const getTeacherRoles = () =>
  api<TeacherRolesResponse>("/api/teacher/roles");
export const getProfile = () => api<Profile>("/api/profile");
export const getSessions = () => api<Session[]>("/api/sessions");
export const getMistakes = () => api<Mistake[]>("/api/mistakes");
export const getLearningCurriculum = () =>
  api<CurriculumResponse>("/api/learning/curriculum");
export const getTodayPlan = () => api<DailyPlan>("/api/learning/today");
export const createDailyPlan = (payload: DailyPlanRequest) =>
  api<DailyPlan>("/api/learning/daily-plan", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const getLearningReviews = () =>
  api<LearningReviewsResponse>("/api/learning/reviews");
export const getLearningSkills = () =>
  api<LearningSkill[]>("/api/learning/skills");
export const recordLearningAttempt = (payload: LearningAttemptCreate) =>
  api<LearningAttemptReceipt>("/api/learning/attempts", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const getLibrarySummary = () =>
  api<LibrarySummary>("/api/library/status");
export const getLaboratorySummary = () =>
  api<LaboratorySummary>("/api/library/laboratory/summary");
export const getLaboratorySources = (filter = "all") =>
  api<LaboratorySource[]>(
    `/api/library/laboratory/sources?filter=${encodeURIComponent(filter)}`,
  );
export const getLaboratorySource = (sourceId: string) =>
  api<LaboratorySourceDetail>(
    `/api/library/laboratory/sources/${encodeURIComponent(sourceId)}`,
  );
export const getDocumentVersions = (sourceId: string) =>
  api<DocumentVersion[]>(
    `/api/library/laboratory/sources/${encodeURIComponent(sourceId)}/versions`,
  );
export const getDocumentVersion = (versionId: number) =>
  api<DocumentVersion>(`/api/library/laboratory/versions/${versionId}`);
export const getVersionComparisons = () =>
  api<VersionComparison[]>("/api/library/laboratory/comparisons?limit=50");
export const createVersionComparison = (
  baseSourceVersionId: number,
  targetSourceVersionId: number,
  algorithmVersion = "page-match.v1",
  window = 12,
) =>
  api<VersionComparison>("/api/library/laboratory/comparisons", {
    method: "POST",
    body: JSON.stringify({
      base_source_version_id: baseSourceVersionId,
      target_source_version_id: targetSourceVersionId,
      algorithm_version: algorithmVersion,
      configuration: { window, minimum_score: 0.55, ocr: false },
      initiated_by: "laboratory",
    }),
  });
export const executeVersionComparison = (comparisonId: string) =>
  api<VersionComparison>(
    `/api/library/laboratory/comparisons/${encodeURIComponent(comparisonId)}/execute`,
    { method: "POST" },
  );
export const getPageCorrespondences = (comparisonId: string, page = 1) =>
  api<PageCorrespondenceList>(
    `/api/library/laboratory/comparisons/${encodeURIComponent(comparisonId)}/correspondences?page=${page}&page_size=50`,
  );
export const confirmPageCorrespondence = (
  comparisonId: string,
  correspondenceId: string,
) =>
  api<PageCorrespondence>(
    `/api/library/laboratory/comparisons/${encodeURIComponent(comparisonId)}/correspondences/${encodeURIComponent(correspondenceId)}/confirm`,
    {
      method: "POST",
      body: JSON.stringify({ actor: "laboratory", note: null }),
    },
  );
export const rejectPageCorrespondence = (
  comparisonId: string,
  correspondenceId: string,
) =>
  api<PageCorrespondence>(
    `/api/library/laboratory/comparisons/${encodeURIComponent(comparisonId)}/correspondences/${encodeURIComponent(correspondenceId)}/reject`,
    {
      method: "POST",
      body: JSON.stringify({ actor: "laboratory", note: null }),
    },
  );
export const adjustPageCorrespondence = (
  comparisonId: string,
  correspondenceId: string,
  payload: {
    base_pages: number[];
    target_pages: number[];
    relation_type: string;
    note: string | null;
  },
) =>
  api<PageCorrespondence>(
    `/api/library/laboratory/comparisons/${encodeURIComponent(comparisonId)}/correspondences/${encodeURIComponent(correspondenceId)}/adjust`,
    {
      method: "POST",
      body: JSON.stringify({ ...payload, actor: "laboratory" }),
    },
  );
export const markPageWithoutEquivalent = (
  comparisonId: string,
  payload: {
    side: "base" | "target";
    page_number: number;
    reason: "inserted" | "deleted" | "blank" | "duplicate" | "unresolved";
  },
) =>
  api<PageCorrespondence>(
    `/api/library/laboratory/comparisons/${encodeURIComponent(comparisonId)}/no-equivalent`,
    {
      method: "POST",
      body: JSON.stringify({
        ...payload,
        actor: "laboratory",
        note: "Decisión manual desde Laboratorio",
      }),
    },
  );
export const getVersionComparisonEvents = (comparisonId: string) =>
  api<ComparisonEvent[]>(
    `/api/library/laboratory/comparisons/${encodeURIComponent(comparisonId)}/events`,
  );
export const revertVersionComparisonEvent = (
  comparisonId: string,
  eventId: string,
) =>
  api<ComparisonEvent>(
    `/api/library/laboratory/comparisons/${encodeURIComponent(comparisonId)}/events/${encodeURIComponent(eventId)}/revert`,
    {
      method: "POST",
      body: JSON.stringify({
        actor: "laboratory",
        note: "Reversión desde el historial",
      }),
    },
  );
export const cancelVersionComparison = (comparisonId: string) =>
  api<VersionComparison>(
    `/api/library/laboratory/comparisons/${encodeURIComponent(comparisonId)}/cancel`,
    { method: "POST" },
  );
export const getComparisonThumbnailUrl = (
  comparisonId: string,
  correspondenceId: string,
  side: "base" | "target",
  position: number,
) =>
  `${API_URL}/api/library/laboratory/comparisons/${encodeURIComponent(comparisonId)}/correspondences/${encodeURIComponent(correspondenceId)}/thumbnail/${side}/${position}`;
export const detectDocumentChanges = () =>
  api<DocumentInventoryResult>("/api/library/laboratory/inventory", {
    method: "POST",
  });
export const getLatestDocumentInventory = () =>
  api<DocumentInventoryResult>("/api/library/laboratory/inventory/latest");
export const getDocumentRuns = () =>
  api<DocumentRun[]>("/api/library/laboratory/runs?limit=50");
export const getDocumentRun = (runId: string) =>
  api<DocumentRunDetail>(
    `/api/library/laboratory/runs/${encodeURIComponent(runId)}`,
  );
export const getDocumentRunPages = (
  runId: string,
  page: number,
  filter = "all",
) =>
  api<DocumentRunPageList>(
    `/api/library/laboratory/runs/${encodeURIComponent(runId)}/pages?page=${page}&page_size=25&filter=${encodeURIComponent(filter)}`,
  );
export const createDocumentRun = (sourceVersionId: number) =>
  api<DocumentRunDetail>("/api/library/laboratory/runs", {
    method: "POST",
    body: JSON.stringify({
      source_version_id: sourceVersionId,
      run_type: "document_analysis",
      pipeline_version: "document-pipeline.v1",
      configuration: { safe_stages_only: true },
      selection_strategy: "all_pages",
      reason: "Ejecución planificada desde el Laboratorio documental",
    }),
  });
export const createStructuredExtractionRun = (sourceVersionId: number) =>
  api<DocumentRunDetail>(
    `/api/library/laboratory/extraction/runs?source_version_id=${sourceVersionId}`,
    { method: "POST" },
  );
const executeStructuredStage = (runId: string, path: string) =>
  api<DocumentRunDetail>(
    `/api/library/laboratory/runs/${encodeURIComponent(runId)}/${path}`,
    { method: "POST" },
  );
export const executeStructuredPreflight = (runId: string) =>
  executeStructuredStage(runId, "pdf-preflight");
export const materializeStructuredPages = (runId: string) =>
  executeStructuredStage(runId, "pages/materialize");
export const extractStructuredText = (runId: string) =>
  executeStructuredStage(runId, "text/extract-embedded");
export const analyzeStructuredLayout = (runId: string) =>
  executeStructuredStage(runId, "layout/analyze");
export const extractStructureCandidates = (runId: string) =>
  executeStructuredStage(runId, "candidates/extract");
export const reconcileStructuredCoverage = (runId: string) =>
  executeStructuredStage(runId, "structured-coverage/reconcile");
export const compareStructuredVersion = (runId: string) =>
  executeStructuredStage(runId, "version-comparison");
export const repeatStructuredPages = (runId: string, pages: number[]) =>
  api<DocumentRunDetail>(
    `/api/library/laboratory/runs/${encodeURIComponent(runId)}/pages/repeat`,
    {
      method: "POST",
      body: JSON.stringify({
        pages,
        reason: "Repetición selectiva desde Extracción",
      }),
    },
  );
export const getStructuredPageBlocks = (runId: string, pageNumber: number) =>
  api<DocumentPageBlock[]>(
    `/api/library/laboratory/runs/${encodeURIComponent(runId)}/pages/${pageNumber}/blocks`,
  );
export const getStructureCandidates = (parameters: URLSearchParams) =>
  api<StructureCandidateList>(
    `/api/library/laboratory/extraction/candidates?${parameters}`,
  );
export const getStructureCandidate = (candidateId: string) =>
  api<StructureCandidate>(
    `/api/library/laboratory/extraction/candidates/${encodeURIComponent(candidateId)}`,
  );
export const getProposedHierarchy = (sourceVersionId: number) =>
  api<ProposedHierarchyNode[]>(
    `/api/library/laboratory/extraction/versions/${sourceVersionId}/hierarchy`,
  );
export const structuredThumbnailUrl = (
  sourceVersionId: number,
  pageNumber: number,
) =>
  `${API_URL}/api/library/laboratory/extraction/versions/${sourceVersionId}/pages/${pageNumber}/thumbnail`;
export const consolidateDocumentReview = (sourceVersionId: number) =>
  api(
    `/api/library/laboratory/review/versions/${sourceVersionId}/consolidate`,
    { method: "POST" },
  );
export const getDocumentReviewSummary = () =>
  api<DocumentReviewSummary>("/api/library/laboratory/review/summary");
export const getDocumentReviewQueue = (parameters: URLSearchParams) =>
  api<DocumentReviewQueue>(
    `/api/library/laboratory/review/queue?${parameters.toString()}`,
  );
export const getDocumentReviewItem = (candidateId: string) =>
  api<DocumentReviewDetail>(
    `/api/library/laboratory/review/items/${encodeURIComponent(candidateId)}`,
  );
export const decideDocumentReviewItem = (
  candidateId: string,
  action: "support" | "confirm" | "reject" | "conflict" | "supersede",
  visual = false,
) =>
  api<DocumentReviewDecision>(
    `/api/library/laboratory/review/items/${encodeURIComponent(candidateId)}/${visual ? "visual-review" : "decision"}`,
    {
      method: "POST",
      body: JSON.stringify({
        action,
        actor: "local_user",
        method: visual ? "visual_review" : "manual_review",
        evidence: visual ? { thumbnail_reviewed: true } : {},
      }),
    },
  );
export const getDocumentReviewBatches = (sourceVersionId?: number) =>
  api<DocumentReviewBatch[]>(
    `/api/library/laboratory/review/batches${sourceVersionId ? `?source_version_id=${sourceVersionId}` : ""}`,
  );
export const applyDocumentReviewBatch = (batchId: string) =>
  api<DocumentReviewBatch>(
    `/api/library/laboratory/review/batches/${encodeURIComponent(batchId)}/apply`,
    {
      method: "POST",
      body: JSON.stringify({ actor: "local_user" }),
    },
  );
export const revertDocumentReviewBatch = (batchId: string) =>
  api<DocumentReviewBatch>(
    `/api/library/laboratory/review/batches/${encodeURIComponent(batchId)}/revert`,
    {
      method: "POST",
      body: JSON.stringify({ actor: "local_user" }),
    },
  );
export const getConsolidatedTopics = (sourceVersionId: number) =>
  api<ConsolidatedTopic[]>(
    `/api/library/laboratory/review/versions/${sourceVersionId}/topics`,
  );
export const getConsolidatedHierarchy = (sourceVersionId: number) =>
  api<ConsolidatedNode[]>(
    `/api/library/laboratory/review/versions/${sourceVersionId}/hierarchy`,
  );
export const getExerciseSolutionRelations = (sourceVersionId: number) =>
  api<ExerciseSolutionRelation[]>(
    `/api/library/laboratory/review/versions/${sourceVersionId}/exercise-solutions`,
  );
export const previewDocumentAuditExport = (
  sourceVersionId: number,
  payload: AuditExportRequest,
) =>
  api<AuditExportPreview>(
    `/api/library/laboratory/audit/versions/${sourceVersionId}/exports/preview`,
    { method: "POST", body: JSON.stringify(payload) },
  );
export const createDocumentAuditExport = (
  sourceVersionId: number,
  payload: AuditExportRequest,
) =>
  api<AuditExport>(
    `/api/library/laboratory/audit/versions/${sourceVersionId}/exports`,
    { method: "POST", body: JSON.stringify(payload) },
  );
export const getDocumentAuditExports = (sourceVersionId?: number) =>
  api<AuditExport[]>(
    `/api/library/laboratory/audit/exports${sourceVersionId ? `?source_version_id=${sourceVersionId}` : ""}`,
  );
export const validateDocumentAuditDecisions = (payload: AuditDecisionSet) =>
  api<AuditValidation>("/api/library/laboratory/audit/imports/validate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const dryRunDocumentAuditDecisions = (payload: AuditDecisionSet) =>
  api<AuditValidation>("/api/library/laboratory/audit/imports/dry-run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const applyDocumentAuditDecisions = (payload: AuditDecisionSet) =>
  api<AuditImport>("/api/library/laboratory/audit/imports/apply?atomic=true", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const getDocumentClosureStatus = (sourceVersionId: number) =>
  api<ClosureStatus>(
    `/api/library/laboratory/audit/versions/${sourceVersionId}/closure`,
  );
export const getDocumentClosureSnapshots = (sourceVersionId?: number) =>
  api<ClosureSnapshot[]>(
    `/api/library/laboratory/audit/snapshots${sourceVersionId ? `?source_version_id=${sourceVersionId}` : ""}`,
  );
export const createDocumentClosureSnapshot = (sourceVersionId: number) =>
  api<ClosureSnapshot>(
    `/api/library/laboratory/audit/versions/${sourceVersionId}/snapshots`,
    { method: "POST" },
  );
export const executeDocumentPreflight = (runId: string) =>
  api<DocumentRunDetail>(
    `/api/library/laboratory/runs/${encodeURIComponent(runId)}/preflight`,
    { method: "POST" },
  );
export const reconcileDocumentCoverage = (runId: string) =>
  api<DocumentRunDetail>(
    `/api/library/laboratory/runs/${encodeURIComponent(runId)}/coverage/reconcile`,
    { method: "POST" },
  );
export const pauseDocumentRun = (runId: string) =>
  api<DocumentRunDetail>(
    `/api/library/laboratory/runs/${encodeURIComponent(runId)}/pause`,
    { method: "POST" },
  );
export const resumeDocumentRun = (runId: string) =>
  api<DocumentRunDetail>(
    `/api/library/laboratory/runs/${encodeURIComponent(runId)}/resume`,
    { method: "POST" },
  );
export const cancelDocumentRun = (runId: string) =>
  api<DocumentRunDetail>(
    `/api/library/laboratory/runs/${encodeURIComponent(runId)}/cancel`,
    { method: "POST" },
  );
export const retryDocumentRunStage = (runId: string, stageName: string) =>
  api<DocumentRunDetail>(
    `/api/library/laboratory/runs/${encodeURIComponent(runId)}/stages/${encodeURIComponent(stageName)}/retry`,
    {
      method: "POST",
      body: JSON.stringify({ failed_pages_only: true }),
    },
  );
export const createDocumentRunPass = (runId: string) =>
  api<DocumentRunDetail>(
    `/api/library/laboratory/runs/${encodeURIComponent(runId)}/passes`,
    {
      method: "POST",
      body: JSON.stringify({
        reason: "Nueva pasada sobre páginas pendientes o problemáticas",
      }),
    },
  );
export const getLibrarySources = () =>
  api<LibrarySource[]>("/api/library/sources?limit=100");
export const getLibraryJobs = () =>
  api<LibraryJob[]>("/api/library/jobs?limit=20");
export const getLibraryKnowledge = () =>
  api<KnowledgeUnit[]>("/api/library/knowledge?limit=50");
export const getLibraryCore = () => api<CoreSourcePair>("/api/library/core");
export const assignLibraryCore = (
  sourceId: string,
  payload: {
    operation_id: string;
    pedagogical_role: "core_theory" | "core_workbook" | "core_answer_key";
    display_alias?: string | null;
    canonical_title?: string | null;
    related_source_id?: string | null;
    editorial_notes?: string | null;
  },
) =>
  api<LibrarySource>(
    `/api/library/sources/${encodeURIComponent(sourceId)}/core`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
export const getLibrarySections = (sourceId: string) =>
  api<EditorialSection[]>(
    `/api/library/sources/${encodeURIComponent(sourceId)}/sections`,
  );
export const buildLibrarySections = (sourceId: string) =>
  api<EditorialSection[]>(
    `/api/library/sources/${encodeURIComponent(sourceId)}/sections/build`,
    { method: "POST" },
  );
export const updateLibrarySection = (
  sectionId: number,
  payload: Partial<
    Pick<
      EditorialSection,
      | "title"
      | "page_start"
      | "page_end"
      | "cefr_min"
      | "cefr_max"
      | "topic"
      | "content_role"
      | "editorial_status"
      | "notes"
    >
  >,
) =>
  api<EditorialSection>(`/api/library/sections/${sectionId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const getLibraryPageQuality = (sourceId: string) =>
  api<PageQuality[]>(
    `/api/library/sources/${encodeURIComponent(sourceId)}/pages/quality`,
  );
export const analyzeLibraryPages = (sourceId: string) =>
  api<PageQuality[]>(
    `/api/library/sources/${encodeURIComponent(sourceId)}/pages/analyze`,
    { method: "POST" },
  );
export const getLibraryModelRoles = () =>
  api<LibraryModelRouting>("/api/library/models/roles");
export const indexLibrarySemantic = () =>
  api<LibraryJob>("/api/library/semantic/index", {
    method: "POST",
    body: JSON.stringify({ batch_size: 16, core_first: true }),
  });
export const getLibrarySourceChunks = (sourceId: string) =>
  api<LibraryChunk[]>(
    `/api/library/sources/${encodeURIComponent(sourceId)}/chunks?limit=50`,
  );
export const getLibrarySourceVersions = (sourceId: string) =>
  api<LibrarySourceVersion[]>(
    `/api/library/sources/${encodeURIComponent(sourceId)}/versions`,
  );
export const scanLibrary = () =>
  api<LibraryJob>("/api/library/scan", {
    method: "POST",
    body: JSON.stringify({ process_documents: true }),
  });
export const processLibraryPending = () =>
  api<LibraryJob>("/api/library/process", { method: "POST" });
export const reprocessLibrarySource = (sourceId: string) =>
  api<LibrarySource>(
    `/api/library/sources/${encodeURIComponent(sourceId)}/reprocess`,
    { method: "POST" },
  );
export const excludeLibrarySource = (sourceId: string) =>
  api<LibrarySource>(
    `/api/library/sources/${encodeURIComponent(sourceId)}/exclude`,
    { method: "POST" },
  );
export const pauseLibraryJob = (jobId: string) =>
  api<LibraryJob>(`/api/library/jobs/${encodeURIComponent(jobId)}/pause`, {
    method: "POST",
  });
export const retryLibraryJob = (jobId: string) =>
  api<LibraryJob>(`/api/library/jobs/${encodeURIComponent(jobId)}/retry`, {
    method: "POST",
  });
export const searchLibrary = (
  query: string,
  mode: "lexical" | "semantic" | "hybrid",
) =>
  api<LibrarySearchResponse>(
    `/api/library/search?query=${encodeURIComponent(query)}&mode=${mode}&limit=20`,
  );
export const reviewKnowledge = (unitId: string, action: "approve" | "reject") =>
  api<KnowledgeUnit>(
    `/api/library/knowledge/${encodeURIComponent(unitId)}/review`,
    {
      method: "POST",
      body: JSON.stringify({ action, comment: null }),
    },
  );
export const generateGroundedDraft = (payload: {
  query: string;
  level: string;
  objective:
    | "explanation"
    | "micro_lesson"
    | "exercises"
    | "answer"
    | "error_explanation";
}) =>
  api<GroundedDraft>("/api/library/grounded/generate", {
    method: "POST",
    body: JSON.stringify({
      ...payload,
      explanation_language: "es",
      max_sources: 5,
    }),
  });

export const askLibrary = (
  payload: {
    question: string;
    conversation_id?: string | null;
    source_id?: string | null;
    continuation_action?:
      | "expand"
      | "more_examples"
      | "rephrase"
      | "use_in_sentence"
      | "follow_up"
      | null;
  },
  signal?: AbortSignal,
) =>
  api<TeacherQuery>("/api/library/ask", {
    method: "POST",
    body: JSON.stringify(payload),
    signal,
  });

export const streamLibraryAnswer = (
  payload: Parameters<typeof askLibrary>[0],
  signal?: AbortSignal,
) =>
  streamSse<TeacherStreamEvent>("/api/library/ask/stream", {
    method: "POST",
    body: JSON.stringify(payload),
    signal,
  });

export const getTeacherQuery = (queryId: string) =>
  api<TeacherQuery>(`/api/library/queries/${encodeURIComponent(queryId)}`);

export const getTeacherConversations = () =>
  api<TeacherConversationSummary[]>("/api/library/conversations?limit=20");

export const getTeacherConversation = (conversationId: string) =>
  api<TeacherQuery[]>(
    `/api/library/conversations/${encodeURIComponent(conversationId)}`,
  );

export async function deleteTeacherConversation(
  conversationId: string,
): Promise<void> {
  await request(
    `/api/library/conversations/${encodeURIComponent(conversationId)}`,
    { method: "DELETE" },
  );
}

export const getPedagogicalMemorySummary = () =>
  api<PedagogicalMemorySummary>("/api/library/memory/status");

export const getPedagogicalConcepts = (query = "") =>
  api<PedagogicalConceptSummary[]>(
    `/api/library/memory/concepts?limit=100${query ? `&query=${encodeURIComponent(query)}` : ""}`,
  );

export const getPedagogicalConcept = (conceptId: string) =>
  api<PedagogicalConcept>(
    `/api/library/memory/concepts/${encodeURIComponent(conceptId)}`,
  );

export const getMemoryReviewQueue = (filters?: {
  target_type?: "concept" | "location";
  status?: string;
  herder_only?: boolean;
  recently_used_only?: boolean;
  current_query_id?: string;
}) => {
  const params = new URLSearchParams({ limit: "10" });
  if (filters?.target_type) params.set("target_type", filters.target_type);
  if (filters?.status) params.set("status", filters.status);
  if (filters?.herder_only) params.set("herder_only", "true");
  if (filters?.recently_used_only) params.set("recently_used_only", "true");
  if (filters?.current_query_id)
    params.set("current_query_id", filters.current_query_id);
  return api<MemoryReviewQueueItem[]>(
    `/api/library/memory/review-queue?${params.toString()}`,
  );
};

export const getMemoryAudit = (targetType: string, targetId: string) =>
  api<MemoryAudit[]>(
    `/api/library/memory/audit?target_type=${encodeURIComponent(targetType)}&target_id=${encodeURIComponent(targetId)}&limit=30`,
  );

export const sendTeacherResponseFeedback = (
  queryId: string,
  verdict: MemoryFeedbackVerdict,
) =>
  api(`/api/library/queries/${encodeURIComponent(queryId)}/feedback`, {
    method: "POST",
    body: JSON.stringify({
      verdict,
      comment: null,
      operation_id: crypto.randomUUID(),
    }),
  });

export const sendTeacherLocationFeedback = (
  queryId: string,
  locationId: string,
  payload: {
    verdict: MemoryFeedbackVerdict;
    comment?: string | null;
    scan_layout?: ScanLayout | null;
    region?: EvidenceRegion | null;
    printed_left_label?: string | null;
    printed_right_label?: string | null;
    printed_full_label?: string | null;
  },
) =>
  api(
    `/api/library/queries/${encodeURIComponent(queryId)}/locations/${encodeURIComponent(locationId)}/feedback`,
    {
      method: "POST",
      body: JSON.stringify({
        ...payload,
        operation_id: crypto.randomUUID(),
      }),
    },
  );

export const reviewPedagogicalMemory = (
  targetType: "concept" | "location" | "relation",
  targetId: string,
  action: "confirm" | "reject" | "unknown" | "postpone",
) =>
  api(
    `/api/library/memory/${targetType}/${encodeURIComponent(targetId)}/review`,
    {
      method: "POST",
      body: JSON.stringify({
        action,
        comment: null,
        operation_id: crypto.randomUUID(),
      }),
    },
  );

export const revertPedagogicalMemoryReview = (reviewId: string) =>
  api(`/api/library/memory/reviews/${encodeURIComponent(reviewId)}/revert`, {
    method: "POST",
    body: JSON.stringify({
      operation_id: crypto.randomUUID(),
      comment: "Reversión solicitada desde Memoria verificada.",
    }),
  });
