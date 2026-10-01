import {
  errorResponse,
  getApplicationStatusHistory,
} from "../../../../../lib/server/api";
import { WebError } from "../../../../../lib/errors";

export async function GET(
  request: Request,
  context: { params: Promise<{ applicationId: string }> },
) {
  try {
    if (new URL(request.url).search) throw new WebError("invalid_request", 400);
    return Response.json(
      await getApplicationStatusHistory((await context.params).applicationId),
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return errorResponse(error);
  }
}
