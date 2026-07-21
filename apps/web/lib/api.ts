import type {
  CurriculumResponse,
  CoreSourcePair,
  DailyPlan,
  DailyPlanRequest,
  Dashboard,
  LearningAttemptCreate,
  LearningAttemptReceipt,
  LearningReviewsResponse,
  LearningSkill,
  GroundedDraft,
  EvidenceRegion,
  KnowledgeUnit,
  LibraryChunk,
  LibraryJob,
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
  PedagogicalConcept,
  PedagogicalConceptSummary,
  PedagogicalMemorySummary,
  Mistake,
  ModelsResponse,
  Profile,
  Session,
  ScanLayout,
  TeacherConversationSummary,
  TeacherQuery,
  TeacherStreamEvent,
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
  model: string | null;
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
