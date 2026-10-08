import { getFollowUps, errorResponse } from "../../../../lib/server/api";
import { WebError } from "../../../../lib/errors";

export async function GET(request: Request) {
  try {
    const params = new URL(request.url).searchParams;
    if (
      [...params.keys()].some(
        (key) =>
          !["timezone", "bucket"].includes(key) ||
          params.getAll(key).length !== 1,
      )
    )
      throw new WebError("invalid_request", 400);
    const timezone = params.get("timezone");
    const bucket = params.get("bucket");
    if (
      !timezone ||
      timezone.length > 128 ||
      !bucket ||
      !["overdue", "today", "upcoming"].includes(bucket)
    )
      throw new WebError("invalid_request", 400);
    return Response.json(await getFollowUps(timezone, bucket), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return errorResponse(error);
  }
}
