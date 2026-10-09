import type {
  NormalisedAnswerResponse,
  NormalisedQuiz,
  QuizSummary,
} from "@/lib/api";
import { ApiError } from "@/lib/api";

export type QuizPhase =
  | "start"
  | "creating"
  | "answering"
  | "submitting"
  | "feedback"
  | "finishing"
  | "review";

/**
 * C2, F3: QuestionResult = Pick<NormalisedAnswerResponse, "answer" | "feedback" | "already_answered">
 */
export type QuestionResult = Pick<
  NormalisedAnswerResponse,
  "answer" | "feedback" | "already_answered"
>;

/**
 * C2: QuizSession has no questions field; use session.quiz.questions everywhere.
 */
export interface QuizSession {
  quiz: NormalisedQuiz;
  index: number;
  results: Record<string, QuestionResult>;
  summary: QuizSummary | null;
  returnPhase?: "feedback" | "answering";
}

/**
 * C1: QuizErrorState is { code, source, message, retryable }
 */
export interface QuizErrorState {
  code: string;
  source: "bank" | "create" | "answer" | "finish";
  message: string;
  retryable: boolean;
}

export function formatQuizError(
  err: unknown,
  source: "bank" | "create" | "answer" | "finish"
): QuizErrorState {
  if (err instanceof ApiError) {
    if (err.code === "not_ready") {
      return {
        code: "not_ready",
        source,
        message: "There are no verified questions for these topics yet.",
        retryable: false,
      };
    }
    if (err.code === "invalid") {
      const msg = err.message?.trim() || "That answer wasn't accepted. Try again.";
      return {
        code: "invalid",
        source,
        message: msg,
        retryable: false,
      };
    }
    if (err.code === "not_found") {
      return {
        code: "not_found",
        source,
        message: "This quiz is no longer available. Start a new one.",
        retryable: false,
      };
    }
    if (err.code === "timeout") {
      return {
        code: "timeout",
        source,
        message: "This is taking longer than it should. Try again in a minute.",
        retryable: true,
      };
    }
    if (err.code === "network_error") {
      return {
        code: "network_error",
        source,
        message: "Couldn't reach the server. Check your connection and try again.",
        retryable: true,
      };
    }
    return {
      code: err.code || "unknown",
      source,
      message: "Something went wrong on the server. Try again in a minute.",
      retryable: false,
    };
  }
  return {
    code: "unknown",
    source,
    message: "Something went wrong on the server. Try again in a minute.",
    retryable: false,
  };
}
