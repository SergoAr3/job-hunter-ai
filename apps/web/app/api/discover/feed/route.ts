import {
  startDiscoverFeed,
  advanceDiscoverFeed,
  getDiscoverFeed,
  feedErrorResponse,
} from "../../../../lib/server/discover-feed";
import { validFeedCursor } from "../../../../lib/discover";
import { requireMutation } from "../../../../lib/server/origin";
import { WebError } from "../../../../lib/errors";
const invalid = () => new WebError("invalid_request", 400);
export async function GET(request: Request) {
  try {
    const params = new URL(request.url).searchParams;
    if (
      [...params.keys()].some((key) => key !== "cursor") ||
      params.getAll("cursor").length !== 1 ||
      !validFeedCursor(params.get("cursor"))
    )
      throw invalid();
    return Response.json(await getDiscoverFeed(params.get("cursor")!), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return feedErrorResponse(error);
  }
}
export async function POST(request: Request) {
  try {
    requireMutation(request);
    if (new URL(request.url).search) throw invalid();
    const text = await request.text();
    if (text.length > 2048) throw invalid();
    let value;
    try {
      value = JSON.parse(text);
    } catch {
      throw invalid();
    }
    if (!value || typeof value !== "object" || Array.isArray(value))
      throw invalid();
    if (Object.keys(value).length === 1 && validFeedCursor(value.cursor))
      return Response.json(await advanceDiscoverFeed(value.cursor), {
        headers: { "Cache-Control": "no-store" },
      });
    if (
      Object.keys(value).length !== 2 ||
      value.remote_only !== true ||
      typeof value.query !== "string" ||
      !value.query.trim() ||
      value.query.trim().length > 200 ||
      /[\x00-\x1f\x7f]/.test(value.query)
    )
      throw invalid();
    return Response.json(
      await startDiscoverFeed({ query: value.query.trim(), remote_only: true }),
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return feedErrorResponse(error);
  }
}
