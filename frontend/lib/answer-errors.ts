import { ApiError } from "./api";

export function formatAskError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "not_ready") {
      return "This notebook has no processed sources yet.";
    }
    if (err.code === "quota_exhausted") {
      if (err.retryAfterS != null) {
        return `The AI model's free quota is used up. Try again in ${err.retryAfterS} seconds.`;
      }
      return "The AI model's free quota is used up. Try again later.";
    }
    if (err.code === "unavailable" || err.status === 503) {
      return "The AI model is busy. Try again in a minute.";
    }
    if (err.code === "timeout") {
      return "The AI model is slow right now. Try again in a minute; if it finished in the background, the answer often comes back straight away.";
    }
    return err.message;
  }
  return "Failed to get an answer.";
}
