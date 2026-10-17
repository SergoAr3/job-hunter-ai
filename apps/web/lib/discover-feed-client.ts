import type { DiscoverFeedPage } from "./contracts";
import { webRequest } from "./client";
export function startFeed(query: string, signal: AbortSignal) {
  return webRequest<DiscoverFeedPage>("/api/discover/feed", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, remote_only: true }),
    signal,
  });
}
export function advanceFeed(cursor: string, signal: AbortSignal) {
  return webRequest<DiscoverFeedPage>("/api/discover/feed", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cursor }),
    signal,
  });
}
export function replayFeed(cursor: string, signal: AbortSignal) {
  return webRequest<DiscoverFeedPage>(
    `/api/discover/feed?cursor=${encodeURIComponent(cursor)}`,
    { signal },
  );
}
