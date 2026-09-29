import { searchJobs, errorResponse } from "../../../lib/server/api";
import { WebError } from "../../../lib/errors";
export async function GET(request: Request) {
  try {
    const params = new URL(request.url).searchParams;
    const query = params.get("query")?.trim() ?? "";
    const offset = params.get("offset") ?? "0";
    if (
      [...params.keys()].some(
        (key) => !["query", "offset", "remote_only"].includes(key),
      ) ||
      !query ||
      query.length > 200 ||
      !/^\d+$/.test(offset) ||
      !Number.isSafeInteger(Number(offset)) ||
      ![null, "true", "false"].includes(params.get("remote_only"))
    )
      throw new WebError("invalid_request", 400);
    return Response.json(
      await searchJobs(
        query,
        Number(offset),
        params.get("remote_only") === "true",
      ),
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return errorResponse(error);
  }
}
