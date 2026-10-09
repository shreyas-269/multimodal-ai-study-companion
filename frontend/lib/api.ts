import { getFirebaseAuth } from "./firebase";
import type { components, operations } from "./api-types";

export type MeResponse = operations["me_get"]["responses"][200]["content"]["application/json"];
export type MePatchBody = operations["me_patch"]["requestBody"]["content"]["application/json"];
export type MePatchResponse = operations["me_patch"]["responses"][200]["content"]["application/json"];

export type NotebookListResponse = operations["notebooks_list"]["responses"][200]["content"]["application/json"];
export type NotebookCreateResponse = operations["notebooks_create"]["responses"][201]["content"]["application/json"];
export type NotebookResponse = operations["notebooks_get"]["responses"][200]["content"]["application/json"];
export type SourceListResponse = operations["sources_list"]["responses"][200]["content"]["application/json"];
export type SourceCreateResponse = operations["sources_create"]["responses"][202]["content"]["application/json"];
export type TopicListResponse = operations["topics_list"]["responses"][200]["content"]["application/json"];
export type TopicSourceListResponse = operations["topics_list_sources"]["responses"][200]["content"]["application/json"];

export type NotebookItem = NotebookListResponse["items"][number];
export type SourceItem = SourceListResponse["items"][number];
export type TopicListItem = TopicListResponse["items"][number];
export type Citation = components["schemas"]["Citation"];
export type SourceRole = components["schemas"]["Body_sources_create"]["role"];

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryAfterS?: number;

  constructor(code: string, message: string, status: number, retryAfterS?: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.retryAfterS = retryAfterS;
  }
}

interface ErrorPayload {
  code: string;
  message: string;
  retry_after_s?: number;
}

function isErrorEnvelope(data: unknown): data is { error: ErrorPayload } {
  if (typeof data !== "object" || data === null) {
    return false;
  }
  const envelope = data as Record<string, unknown>;
  if (typeof envelope.error !== "object" || envelope.error === null) {
    return false;
  }
  const err = envelope.error as Record<string, unknown>;
  return typeof err.code === "string" && typeof err.message === "string";
}

function getApiBaseUrl(): string {
  const baseUrlEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!baseUrlEnv) {
    throw new Error("Missing required environment variable: NEXT_PUBLIC_API_BASE_URL");
  }
  return baseUrlEnv.replace(/\/+$/, "");
}

async function getAuthToken(): Promise<string | null> {
  if (typeof window === "undefined") {
    return null;
  }
  const auth = getFirebaseAuth();
  await auth.authStateReady();
  if (!auth.currentUser) {
    return null;
  }
  return auth.currentUser.getIdToken();
}

export async function apiFetch<T>(
  path: string,
  options?: {
    method?: string;
    body?: unknown;
    headers?: Record<string, string>;
    signal?: AbortSignal;
    auth?: boolean;
    timeoutMessage?: string;
  }
): Promise<T> {
  if (!path.startsWith("/")) {
    throw new Error(`apiFetch path must start with '/': got '${path}'`);
  }

  const baseUrl = getApiBaseUrl();
  const url = `${baseUrl}${path}`;

  const headers: Record<string, string> = { ...(options?.headers ?? {}) };

  if (options?.auth !== false) {
    const token = await getAuthToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
  }

  let requestBody: BodyInit | undefined;
  if (options?.body instanceof FormData) {
    requestBody = options.body;
  } else if (options?.body !== undefined) {
    if (!headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    requestBody = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: options?.method ?? (options?.body !== undefined ? "POST" : "GET"),
      headers,
      body: requestBody,
      signal: options?.signal,
    });
  } catch (err: unknown) {
    if (err instanceof Error) {
      if (err.name === "TimeoutError") {
        throw new ApiError("timeout", options?.timeoutMessage ?? "The request took too long. Try again.", 0);
      }
      if (err.name === "AbortError") {
        throw new ApiError("aborted", "Request was cancelled", 0);
      }
    }
    throw new ApiError("network_error", "Could not reach the backend", 0);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  if (response.ok) {
    return (await response.json()) as T;
  }

  let errorData: unknown = null;
  try {
    errorData = await response.json();
  } catch {
    // Response body is not valid JSON (e.g. HTML 502, empty body)
  }

  if (isErrorEnvelope(errorData)) {
    const retryAfterS =
      typeof errorData.error.retry_after_s === "number"
        ? errorData.error.retry_after_s
        : undefined;
    throw new ApiError(errorData.error.code, errorData.error.message, response.status, retryAfterS);
  }

  throw new ApiError("http_error", `HTTP ${response.status}`, response.status);
}

export async function getMe(): Promise<MeResponse> {
  return apiFetch<MeResponse>("/v1/me");
}

export async function patchMe(body: MePatchBody): Promise<MePatchResponse> {
  return apiFetch<MePatchResponse>("/v1/me", {
    method: "PATCH",
    body,
  });
}

export async function listNotebooks(cursor?: string): Promise<NotebookListResponse> {
  const query = new URLSearchParams({ limit: "20" });
  if (cursor) query.set("cursor", cursor);
  return apiFetch<NotebookListResponse>(`/v1/notebooks?${query.toString()}`);
}

export async function createNotebook(name: string): Promise<NotebookCreateResponse> {
  return apiFetch<NotebookCreateResponse>("/v1/notebooks", {
    method: "POST",
    body: { name },
  });
}

export async function getNotebook(notebookId: string): Promise<NotebookResponse> {
  return apiFetch<NotebookResponse>(`/v1/notebooks/${encodeURIComponent(notebookId)}`);
}

export async function listSources(notebookId: string, cursor?: string): Promise<SourceListResponse> {
  const query = new URLSearchParams({ limit: "20" });
  if (cursor) query.set("cursor", cursor);
  return apiFetch<SourceListResponse>(
    `/v1/notebooks/${encodeURIComponent(notebookId)}/sources?${query.toString()}`
  );
}

export async function uploadSource(
  notebookId: string,
  file: File,
  role: SourceRole = "content"
): Promise<SourceCreateResponse> {
  const formData = new FormData();
  const fileKey: keyof components["schemas"]["Body_sources_create"] = "file";
  formData.append(fileKey, file);
  formData.append("role", role);
  return apiFetch<SourceCreateResponse>(
    `/v1/notebooks/${encodeURIComponent(notebookId)}/sources`,
    {
      method: "POST",
      body: formData,
      signal: AbortSignal.timeout(180000),
      timeoutMessage: "Processing took longer than 3 minutes. Check the sources list in a minute; it may still finish.",
    }
  );
}

export async function listTopics(notebookId: string): Promise<TopicListResponse> {
  return apiFetch<TopicListResponse>(
    `/v1/notebooks/${encodeURIComponent(notebookId)}/topics`
  );
}

export async function listTopicSources(
  notebookId: string,
  topicId: string
): Promise<Citation[]> {
  const data = await apiFetch<TopicSourceListResponse>(
    `/v1/notebooks/${encodeURIComponent(notebookId)}/topics/${encodeURIComponent(topicId)}/sources`
  );
  return data.items;
}

/**
 * Builds the stream URL and headers for a source's viewer PDF.
 *
 * NOTE: pdf.js performs the HTTP GET request itself (including Range header
 * requests for progressive streaming); our code does not call fetch directly.
 * This is the one documented exception to "only lib/api.ts calls fetch",
 * because the target URL and Authorization token are still constructed
 * strictly within lib/api.ts.
 */
export async function getSourceFileRequest(
  notebookId: string,
  sourceId: string
): Promise<{ url: string; httpHeaders: Record<string, string> }> {
  const baseUrl = getApiBaseUrl();
  const token = await getAuthToken();
  if (!token) {
    throw new ApiError("unauthenticated", "Sign in again to open this PDF.", 401);
  }
  const url = `${baseUrl}/v1/notebooks/${encodeURIComponent(notebookId)}/sources/${encodeURIComponent(sourceId)}/file`;
  return {
    url,
    httpHeaders: {
      Authorization: `Bearer ${token}`,
    },
  };
}

export type AskRequest = operations["ask_post"]["requestBody"]["content"]["application/json"];
export type AskResponse = operations["ask_post"]["responses"][200]["content"]["application/json"];
export type ParagraphItem = AskResponse["paragraphs"][number];
export type CitationItem = NonNullable<ParagraphItem["citations"]>[number];
export type ContextChunkItem = AskResponse["context"][number];

/**
 * Submits a question to the notebook ask endpoint.
 */
export async function ask(
  notebookId: string,
  body: AskRequest
): Promise<AskResponse> {
  return apiFetch<AskResponse>(
    `/v1/notebooks/${encodeURIComponent(notebookId)}/ask`,
    {
      method: "POST",
      body,
      signal: AbortSignal.timeout(180000),
      timeoutMessage:
        "The AI model is slow right now. Try again in a minute; if it finished in the background, the answer often comes back straight away.",
    }
  );
}

// Raw Quiz Types from api-types
export type QuestionBankResponse = operations["question_bank_get"]["responses"][200]["content"]["application/json"];
export type QuestionBankItem = components["schemas"]["QuestionBankItem"];

export type QuizCreateRequest = operations["quizzes_create"]["requestBody"]["content"]["application/json"];
export type RawQuizOut = operations["quizzes_create"]["responses"][201]["content"]["application/json"];
export type RawQuestionOut = components["schemas"]["QuestionOut"];
export type QuestionOptionOut = components["schemas"]["QuestionOptionOut"];

export type QuizAnswerRequest = operations["quizzes_answer"]["requestBody"]["content"]["application/json"];
export type RawQuizAnswerResponse = operations["quizzes_answer"]["responses"][200]["content"]["application/json"];
export type RawFeedback = components["schemas"]["Feedback"];

export type QuizSummary = operations["quizzes_finish"]["responses"][200]["content"]["application/json"];
export type TopicSummaryItem = components["schemas"]["TopicSummaryItem"];

// Normalised Types (derived via Omit from generated schemas)
export type NormalisedQuestion = Omit<RawQuestionOut, "options"> & {
  options: QuestionOptionOut[];
};

export type NormalisedFeedback = Omit<
  RawFeedback,
  "correct_option_id" | "citations" | "misconception" | "rubric_coverage"
> & {
  correct_option_id: string | null;
  citations: Citation[];
  misconception: string | null;
};

export type NormalisedQuiz = Omit<RawQuizOut, "questions" | "answers" | "summary"> & {
  questions: NormalisedQuestion[];
};

export type NormalisedAnswerResponse = Omit<RawQuizAnswerResponse, "feedback"> & {
  feedback: NormalisedFeedback;
};

// Normalisation Adapter Functions
export function normaliseQuestion(q: RawQuestionOut): NormalisedQuestion {
  return {
    id: q.id,
    type: q.type,
    topic_id: q.topic_id,
    difficulty: q.difficulty,
    stem: q.stem,
    options: q.options ?? [],
  };
}

export function normaliseFeedback(f: RawFeedback): NormalisedFeedback {
  return {
    verdict: f.verdict,
    correct_answer: f.correct_answer,
    correct_option_id: f.correct_option_id ?? null,
    explanation: f.explanation,
    citations: f.citations ?? [],
    misconception: f.misconception ?? null,
  };
}

export function normaliseQuiz(quiz: RawQuizOut): NormalisedQuiz {
  return {
    id: quiz.id,
    mode: quiz.mode,
    topic_ids: quiz.topic_ids,
    status: quiz.status,
    created_at: quiz.created_at,
    questions: quiz.questions.map(normaliseQuestion),
  };
}

export function normaliseAnswerResponse(res: RawQuizAnswerResponse): NormalisedAnswerResponse {
  return {
    question_id: res.question_id,
    answer: res.answer,
    already_answered: res.already_answered,
    feedback: normaliseFeedback(res.feedback),
  };
}

// Quiz Endpoints
export async function getQuestionBank(notebookId: string): Promise<QuestionBankResponse> {
  return apiFetch<QuestionBankResponse>(
    `/v1/notebooks/${encodeURIComponent(notebookId)}/question-bank`,
    {
      signal: AbortSignal.timeout(30000),
      timeoutMessage: "This is taking longer than it should. Try again in a minute.",
    }
  );
}

export async function createQuiz(
  notebookId: string,
  body: QuizCreateRequest
): Promise<NormalisedQuiz> {
  const raw = await apiFetch<RawQuizOut>(
    `/v1/notebooks/${encodeURIComponent(notebookId)}/quizzes`,
    {
      method: "POST",
      body,
      signal: AbortSignal.timeout(30000),
      timeoutMessage: "This is taking longer than it should. Try again in a minute.",
    }
  );
  return normaliseQuiz(raw);
}

export async function answerQuizQuestion(
  notebookId: string,
  quizId: string,
  body: QuizAnswerRequest
): Promise<NormalisedAnswerResponse> {
  const raw = await apiFetch<RawQuizAnswerResponse>(
    `/v1/notebooks/${encodeURIComponent(notebookId)}/quizzes/${encodeURIComponent(quizId)}/answers`,
    {
      method: "POST",
      body,
      signal: AbortSignal.timeout(30000),
      timeoutMessage: "This is taking longer than it should. Try again in a minute.",
    }
  );
  return normaliseAnswerResponse(raw);
}

export async function finishQuiz(
  notebookId: string,
  quizId: string
): Promise<QuizSummary> {
  return apiFetch<QuizSummary>(
    `/v1/notebooks/${encodeURIComponent(notebookId)}/quizzes/${encodeURIComponent(quizId)}/finish`,
    {
      method: "POST",
      signal: AbortSignal.timeout(30000),
      timeoutMessage: "This is taking longer than it should. Try again in a minute.",
    }
  );
}


