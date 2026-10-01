import { listApplications, errorResponse } from "../../../lib/server/api";
import { parseApplicationsState } from "../../../lib/applications";
import { WebError } from "../../../lib/errors";

export async function GET(request: Request) {
  try {
    const params = new URL(request.url).searchParams;
    const allowed = new Set(["q", "status", "sort", "limit", "offset"]);
    for (const key of params.keys()) {
      if (!allowed.has(key) || params.getAll(key).length !== 1)
        throw new WebError("invalid_request", 400);
    }
    if (params.has("limit") && params.get("limit") !== "5")
      throw new WebError("invalid_request", 400);
    const state = parseApplicationsState(params);
    if (
      (params.has("q") &&
        (params.get("q")!.trim().length > 100 ||
          params.get("q")!.includes("\0"))) ||
      (params.has("status") && state.status === null) ||
      (params.has("sort") &&
        !["newest", "oldest", "next_action"].includes(params.get("sort")!)) ||
      (params.has("offset") &&
        (!/^\d+$/.test(params.get("offset")!) ||
          Number(params.get("offset")) !== state.offset))
    )
      throw new WebError("invalid_request", 400);
    return Response.json(await listApplications(state), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return errorResponse(error);
  }
}
