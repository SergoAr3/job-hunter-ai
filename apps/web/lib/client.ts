import { loginUrl } from "./auth";
import { WebError } from "./errors";
let redirecting = false;
export async function webRequest<T>(
  url: string,
  init?: RequestInit,
): Promise<T> {
  try {
    const response = await fetch(url, { ...init, cache: "no-store" });
    const value = await response.json();
    if (
      response.status === 401 &&
      value?.code === "unauthenticated" &&
      !url.startsWith("/api/auth/") &&
      typeof window !== "undefined" &&
      !["/login", "/register"].includes(window.location.pathname) &&
      !redirecting
    ) {
      redirecting = true;
      window.location.assign(
        loginUrl(window.location.pathname + window.location.search),
      );
    }
    if (!response.ok)
      throw new WebError(
        typeof value?.code === "string" ? value.code : "api_unavailable",
        response.status,
        ["profile_invalid", "auth_invalid"].includes(value?.code) &&
          value?.fieldErrors &&
          typeof value.fieldErrors === "object" &&
          !Array.isArray(value.fieldErrors)
          ? value.fieldErrors
          : undefined,
      );
    return value;
  } catch (error) {
    if (error instanceof WebError) throw error;
    if (url.startsWith("/api/auth/"))
      throw new WebError("auth_unavailable", 503);
    throw new WebError(
      init?.method === "POST"
        ? "ambiguous_save"
        : init?.method === "PUT"
          ? url === "/api/profile"
            ? "ambiguous_profile"
            : "ambiguous_status"
          : "api_unavailable",
    );
  }
}
