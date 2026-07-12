import type {
  CurriculumResponse,
  DailyPlan,
  DailyPlanRequest,
  Dashboard,
  LearningAttemptCreate,
  LearningAttemptReceipt,
  LearningReviewsResponse,
  LearningSkill,
  Mistake,
  ModelsResponse,
  Profile,
  Session,
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
