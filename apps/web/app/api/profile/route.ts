import { requireMutation } from "../../../lib/server/origin";
import { getProfile, putProfile } from "../../../lib/server/profile";
import { errorResponse } from "../../../lib/server/api";
import { WebError } from "../../../lib/errors";
import { validateProfileInput } from "../../../lib/profile";

export async function GET(request: Request) {
  try {
    if (new URL(request.url).search) throw new WebError("invalid_request", 400);
    return Response.json(await getProfile(), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return errorResponse(error);
  }
}

export async function PUT(request: Request) {
  try {
    if (new URL(request.url).search) throw new WebError("invalid_request", 400);
    requireMutation(request);
    let value: unknown;
    try {
      value = JSON.parse(await request.text());
    } catch {
      throw new WebError("invalid_request", 400);
    }
    const result = validateProfileInput(value);
    if (result.invalidShape) throw new WebError("profile_invalid", 422);
    if (!result.payload)
      throw new WebError("profile_invalid", 422, result.errors);
    return Response.json(await putProfile(result.payload), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return errorResponse(error);
  }
}
