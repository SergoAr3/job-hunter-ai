import "server-only";
import { getDomainIdentity } from "./auth";
import { projectDiscoverFeed } from "../discover-feed";
import { validFeedCursor } from "../discover";
import { WebError } from "../errors";
import { errorResponse } from "./api";

export const FEED_POST_TIMEOUT = 30000;
export const FEED_REPLAY_TIMEOUT = 10000;
export interface FeedStart {
  query: string;
  remote_only: true;
}
async function feedRequest(
  body?: FeedStart | { cursor: string },
  cursor?: string,
) {
  const { userId, baseUrl, headers } = await getDomainIdentity();
  let response: Response;
  try {
    response = await fetch(
      `${baseUrl}/users/${userId}/discover/feed${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
      {
        method: body ? "POST" : "GET",
        headers: {
          ...headers,
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(
          body ? FEED_POST_TIMEOUT : FEED_REPLAY_TIMEOUT,
        ),
      },
    );
  } catch {
    throw new WebError("feed_unavailable", 503);
  }
  if (response.status === 401) throw new WebError("unauthenticated", 401);
  let value;
  try {
    value = await response.json();
  } catch {
    throw new WebError("feed_unavailable", 502);
  }
  if (!response.ok) {
    const code = value?.detail?.code;
    const unavailable =
      (response.status === 410 && code === "FEED_CURSOR_EXPIRED") ||
      (response.status === 404 && code === "FEED_CURSOR_UNKNOWN");
    const db =
      (response.status === 409 &&
        ["FEED_DB_LOCK_TIMEOUT", "FEED_ADVANCE_IN_PROGRESS"].includes(code)) ||
      (response.status === 503 && code === "FEED_DB_QUERY_TIMEOUT");
    const source = [
      "source_timeout",
      "source_unavailable",
      "source_rate_limited",
      "source_bad_response",
    ].includes(code);
    const retryCursor = validFeedCursor(value?.detail?.retry_cursor)
      ? value.detail.retry_cursor
      : undefined;
    const retryAfter = response.headers.get("retry-after") ?? undefined;
    throw new WebError(
      unavailable
        ? "feed_expired"
        : db
          ? "feed_busy"
          : source
            ? code
            : "feed_unavailable",
      response.status,
      undefined,
      db && retryAfter && /^\d{1,3}$/.test(retryAfter) ? retryAfter : undefined,
      retryCursor,
    );
  }
  const page = projectDiscoverFeed(value);
  const expected =
    cursor ?? (body && "cursor" in body ? body.cursor : undefined);
  if (expected && page.cursor !== expected)
    throw new WebError("feed_unavailable", 502);
  return page;
}
export function startDiscoverFeed(body: FeedStart) {
  return feedRequest(body);
}
export function advanceDiscoverFeed(cursor: string) {
  return feedRequest({ cursor });
}
export function getDiscoverFeed(cursor: string) {
  return feedRequest(undefined, cursor);
}
export async function feedErrorResponse(error: unknown) {
  const safe =
    error instanceof WebError ? error : new WebError("feed_unavailable", 502);
  const response = await errorResponse(safe);
  if (safe.retryAfter) response.headers.set("Retry-After", safe.retryAfter);
  if (!safe.retryCursor) return response;
  return Response.json(
    { code: safe.code, retry_cursor: safe.retryCursor },
    { status: safe.status, headers: response.headers },
  );
}
