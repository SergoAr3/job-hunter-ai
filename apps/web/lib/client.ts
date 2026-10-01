import { WebError } from "./errors";
export async function webRequest<T>(
  url: string,
  init?: RequestInit,
): Promise<T> {
  try {
    const response = await fetch(url, { ...init, cache: "no-store" });
    const value = await response.json();
    if (!response.ok)
      throw new WebError(
        typeof value?.code === "string" ? value.code : "api_unavailable",
        response.status,
        value?.code === "profile_invalid" &&
          value?.fieldErrors &&
          typeof value.fieldErrors === "object" &&
          !Array.isArray(value.fieldErrors)
          ? value.fieldErrors
          : undefined,
      );
    return value;
  } catch (error) {
    if (error instanceof WebError) throw error;
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
