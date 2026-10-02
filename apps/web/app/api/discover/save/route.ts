import { requireMutation } from "../../../../lib/server/origin";
import { saveJob, errorResponse } from "../../../../lib/server/api";
import { WebError } from "../../../../lib/errors";
export async function POST(request: Request) {
  try {
    requireMutation(request);
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
