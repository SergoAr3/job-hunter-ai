import { getExperienceFacts } from "../../../../lib/server/profile";
import { errorResponse } from "../../../../lib/server/api";
import { WebError } from "../../../../lib/errors";

export async function GET(request: Request) {
  try {
    if (new URL(request.url).search) throw new WebError("invalid_request", 400);
    return Response.json(await getExperienceFacts(), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return errorResponse(error);
  }
}
