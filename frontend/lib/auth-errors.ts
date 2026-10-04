const AUTH_ERROR_MESSAGES: Record<string, string> = {
  "auth/invalid-credential": "Invalid email or password.",
  "auth/user-not-found": "No account found with this email.",
  "auth/wrong-password": "Incorrect password.",
  "auth/email-already-in-use": "An account already exists with this email.",
  "auth/invalid-email": "Please enter a valid email address.",
  "auth/weak-password": "Password is too weak. Please use at least 6 characters.",
  "auth/missing-password": "Please enter a password.",
  "auth/user-disabled": "This account has been disabled.",
  "auth/too-many-requests": "Too many failed attempts. Please try again later.",
  "auth/network-request-failed": "Network error. Please check your connection.",
  "auth/operation-not-allowed": "This sign-in method is not enabled.",
};

const DEFAULT_ERROR_MESSAGE = "An unexpected authentication error occurred. Please try again.";

export function getAuthErrorMessage(error: unknown): string {
  if (typeof error === "object" && error !== null && "code" in error) {
    const code = String((error as { code: unknown }).code);
    if (code in AUTH_ERROR_MESSAGES) {
      return AUTH_ERROR_MESSAGES[code];
    }
  }
  return DEFAULT_ERROR_MESSAGE;
}
