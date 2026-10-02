import { requireMutation } from "../../../../../lib/server/origin";
import {
  applicationStatuses,
  type ApplicationStatus,
} from "../../../../../lib/applications";
import {
  errorResponse,
  setApplicationStatus,
} from "../../../../../lib/server/api";
import { WebError } from "../../../../../lib/errors";

export async function PUT(
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
      Object.keys(value).length !== 1 ||
      !("status" in value) ||
      typeof value.status !== "string" ||
      !applicationStatuses.includes(value.status as ApplicationStatus)
    )
      throw new WebError("status_invalid", 422);
    return Response.json(
      await setApplicationStatus(
        applicationId,
        value.status as ApplicationStatus,
      ),
      {
        headers: { "Cache-Control": "no-store" },
      },
    );
  } catch (error) {
    return errorResponse(error);
  }
}
