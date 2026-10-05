import { getWorkExperiences } from "../../../../lib/server/profile";
import { errorResponse } from "../../../../lib/server/api";
import { WebError } from "../../../../lib/errors";
import {
  mutateWork,
  workMutationBody,
} from "../../../../lib/server/work-experiences";

export async function GET(request: Request) {
  try {
    if (new URL(request.url).search) throw new WebError("invalid_request", 400);
    return Response.json(await getWorkExperiences(), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return errorResponse(error);
  }
}

export async function POST(request: Request) {
  try {
    return Response.json(
      await mutateWork("POST", await workMutationBody(request)),
      {
        status: 201,
        headers: { "Cache-Control": "no-store" },
      },
    );
  } catch (error) {
    return errorResponse(error);
  }
}
