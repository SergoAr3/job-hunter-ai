import { requireMutation } from "../../../../lib/server/origin";
import { WebError } from "../../../../lib/errors";
import {
  patchApplication,
  getApplication,
  errorResponse,
} from "../../../../lib/server/api";
export async function GET(
  _request: Request,
  context: { params: Promise<{ applicationId: string }> },
) {
  try {
    return Response.json(
      await getApplication((await context.params).applicationId),
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return errorResponse(error);
  }
}

export async function PATCH(
  request: Request,
  context: { params: Promise<{ applicationId: string }> },
) {
  try {
    const { applicationId } = await context.params;
    if (
      !/^[1-9]\d*$/.test(applicationId) ||
      !Number.isSafeInteger(Number(applicationId))
    )
      throw new WebError("APPLICATION_NOT_FOUND", 404);
    if (new URL(request.url).search) throw new WebError("invalid_request", 400);
    requireMutation(request);
    let value: unknown;
    try {
      value = JSON.parse(await request.text());
    } catch {
      throw new WebError("invalid_request", 400);
    }
    if (
      !value ||
      typeof value !== "object" ||
      Array.isArray(value) ||
      Object.keys(value).some(
        (key) => !["note", "next_action"].includes(key),
      ) ||
      Object.values(value).some(
        (field) => field !== null && typeof field !== "string",
      )
    )
      throw new WebError("application_invalid", 422);
    return Response.json(await patchApplication(applicationId, value), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return errorResponse(error);
  }
}
