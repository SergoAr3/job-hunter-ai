import { errorResponse } from "../../../../../lib/server/api";
import { WebError } from "../../../../../lib/errors";
import {
  mutateWork,
  workMutationBody,
} from "../../../../../lib/server/work-experiences";

type Context = { params: Promise<{ experienceId: string }> };
export async function PATCH(request: Request, context: Context) {
  try {
    return Response.json(
      await mutateWork(
        "PATCH",
        await workMutationBody(request),
        (await context.params).experienceId,
      ),
      {
        headers: { "Cache-Control": "no-store" },
      },
    );
  } catch (error) {
    return errorResponse(error);
  }
}
export async function DELETE(request: Request, context: Context) {
  try {
    // DELETE accepts the same bounded, same-origin JSON envelope, empty only.
    const body = await workMutationBody(request);
    if (Object.keys(body).length) {
      throw new WebError("invalid_request", 400);
    }
    return Response.json(
      await mutateWork("DELETE", {}, (await context.params).experienceId),
      {
        headers: { "Cache-Control": "no-store" },
      },
    );
  } catch (error) {
    return errorResponse(error);
  }
}
