import { saveJob, errorResponse } from "../../../../lib/server/api";
import { WebError } from "../../../../lib/errors";
export async function POST(request: Request) {
  try {
    // This local mutation is same-origin; it is not an authentication mechanism.
    // Next may construct request.url with its internal hostname. Host is the
    // browser-facing authority; do not trust arbitrary forwarded-host headers.
    const expectedOrigin = new URL(request.url);
    const host = request.headers.get("host");
    if (host) expectedOrigin.host = host;
    if (
      request.headers.get("origin") !== expectedOrigin.origin ||
      request.headers.get("sec-fetch-site") === "cross-site"
    )
      throw new WebError("invalid_request", 403);
    if (!request.headers.get("content-type")?.startsWith("application/json"))
      throw new WebError("invalid_request", 400);
    let value;
    try {
      value = JSON.parse(await request.text());
    } catch {
      throw new WebError("invalid_request", 400);
    }
    if (
      !value ||
      typeof value !== "object" ||
      Object.keys(value).length !== 3 ||
      !["source", "source_scope", "external_id"].every(
        (key) =>
          typeof value[key] === "string" &&
          value[key].length > 0 &&
          value[key].length <= (key === "source" ? 32 : 255),
      )
    )
      throw new WebError("invalid_request", 400);
    const { source, source_scope, external_id } = value;
    return Response.json(await saveJob({ source, source_scope, external_id }), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return errorResponse(error);
  }
}
