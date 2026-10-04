import "server-only";
import { WebError } from "../errors";
export function requireMutation(
  request: Request,
  contentType: "json" | "multipart" = "json",
) {
  const allowed = new Set<string>();
  const configured = process.env.WEB_PUBLIC_ORIGIN;
  if (configured) {
    try {
      const url = new URL(configured);
      if (
        url.origin !== configured ||
        !["http:", "https:"].includes(url.protocol) ||
        ((process.env.NODE_ENV === "production" ||
          process.env.APP_ENV === "production") &&
          url.protocol !== "https:")
      )
        throw new Error();
      allowed.add(url.origin);
    } catch {
      throw new WebError("configuration", 503);
    }
  }
  if (
    process.env.APP_ENV === "development" &&
    process.env.NODE_ENV !== "production"
  ) {
    allowed.add("http://127.0.0.1:3100");
    allowed.add("http://localhost:3100");
  }
  const origin = request.headers.get("origin");
  if (
    !origin ||
    !allowed.has(origin) ||
    request.headers.get("sec-fetch-site") === "cross-site"
  )
    throw new WebError("invalid_request", 403);
  if (
    !(
      contentType === "json"
        ? /^application\/json(?:\s*;.*)?$/i
        : /^multipart\/form-data\s*;.*boundary=/i
    ).test(request.headers.get("content-type") ?? "")
  )
    throw new WebError("invalid_request", 400);
}
