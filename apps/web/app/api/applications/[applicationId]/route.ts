import { getApplication, errorResponse } from "../../../../lib/server/api";
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
