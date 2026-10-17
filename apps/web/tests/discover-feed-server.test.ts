import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { feedPage } from "./fixtures";
import { projectDiscoverFeed } from "../lib/discover-feed";
vi.mock("server-only", () => ({}));
vi.mock("../lib/server/auth", () => ({
  getDomainIdentity: vi.fn(async () => ({
    userId: 7,
    baseUrl: "http://api.test",
    headers: { Authorization: "Bearer server-only-test" },
  })),
}));
import {
  startDiscoverFeed,
  advanceDiscoverFeed,
  getDiscoverFeed,
  FEED_POST_TIMEOUT,
  FEED_REPLAY_TIMEOUT,
} from "../lib/server/discover-feed";
import { GET, POST } from "../app/api/discover/feed/route";
const fetcher = vi.fn();
const A = feedPage.cursor,
  B = feedPage.next_cursor!;
beforeEach(() => {
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "http://web.test");
  vi.stubGlobal("fetch", fetcher);
  fetcher
    .mockReset()
    .mockImplementation(() => Promise.resolve(Response.json(feedPage)));
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});
const request = (value: unknown, path = "http://web.test/api/discover/feed") =>
  new Request(path, {
    method: "POST",
    headers: { origin: "http://web.test", "content-type": "application/json" },
    body: JSON.stringify(value),
  });
it("uses explicit helpers, server identity, bounded timeouts, no-store and no retries", async () => {
  const timers = vi.spyOn(AbortSignal, "timeout");
  await startDiscoverFeed({ query: "Python", remote_only: true });
  await advanceDiscoverFeed(A);
  await getDiscoverFeed(A);
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
    "http://api.test/users/7/discover/feed",
    "http://api.test/users/7/discover/feed",
    `http://api.test/users/7/discover/feed?cursor=${A}`,
  ]);
  expect(fetcher.mock.calls.map(([, init]) => init.method)).toEqual([
    "POST",
    "POST",
    "GET",
  ]);
  expect(fetcher.mock.calls.map(([, init]) => init.cache)).toEqual([
    "no-store",
    "no-store",
    "no-store",
  ]);
  expect(timers.mock.calls).toEqual([
    [FEED_POST_TIMEOUT],
    [FEED_POST_TIMEOUT],
    [FEED_REPLAY_TIMEOUT],
  ]);
  expect(FEED_POST_TIMEOUT).toBeGreaterThan(15000);
  expect(FEED_POST_TIMEOUT).toBeLessThanOrEqual(35000);
  expect(fetcher.mock.calls[0][1].headers.Authorization).toBe(
    "Bearer server-only-test",
  );
});
it("projects only the display contract and accepts GET terminal paused navigation", () => {
  expect(
    projectDiscoverFeed({
      ...feedPage,
      user_id: 7,
      chain_graph: "secret",
      items: [{ ...feedPage.items[0], profile: { private: true } }],
    }),
  ).toEqual(feedPage);
  expect(
    projectDiscoverFeed({
      ...feedPage,
      items: [],
      returned_count: 0,
      state: "paused",
      next_cursor: null,
      next_action: null,
    }).next_action,
  ).toBeNull();
});
it.each([
  { cursor: "bad" },
  { cursor: A + "x" },
  { prev_cursor: "bad" },
  { next_cursor: "bad" },
  { next_action: "claimed" },
  { next_action: null },
  { next_cursor: null },
  { state: "ready" },
  { returned_count: 2 },
  { returned_count: "1" },
  { items: [] },
  { expires_at: "bad" },
  { expires_at: "2026-10-17" },
  { state: "end" },
  { items: [{ ...feedPage.items[0], workplace_type: "unknown" }] },
  { items: [{ ...feedPage.items[0], preview_match: {} }] },
])("rejects malformed feed response %j", async (change) => {
  fetcher.mockResolvedValue(Response.json({ ...feedPage, ...change }));
  await expect(getDiscoverFeed(A)).rejects.toMatchObject({
    code: "feed_unavailable",
  });
});
it("rejects a replay/advance response for a different cursor", async () => {
  fetcher.mockResolvedValue(
    Response.json({ ...feedPage, cursor: "Z".repeat(32) }),
  );
  await expect(getDiscoverFeed(A)).rejects.toMatchObject({
    code: "feed_unavailable",
  });
  await expect(advanceDiscoverFeed(A)).rejects.toMatchObject({
    code: "feed_unavailable",
  });
});
it.each([
  [404, "FEED_CURSOR_UNKNOWN", "feed_expired"],
  [410, "FEED_CURSOR_EXPIRED", "feed_expired"],
  [404, "USER_NOT_FOUND", "feed_unavailable"],
  [404, "arbitrary internal SQL", "feed_unavailable"],
  [409, "FEED_DB_LOCK_TIMEOUT", "feed_busy"],
  [503, "FEED_DB_QUERY_TIMEOUT", "feed_busy"],
  [409, "FEED_ADVANCE_IN_PROGRESS", "feed_busy"],
  [504, "source_timeout", "source_timeout"],
  [502, "source_bad_response", "source_bad_response"],
] as const)("normalizes API %s/%s to %s", async (status, code, expected) => {
  fetcher.mockResolvedValue(
    Response.json(
      { detail: { code, retry_cursor: B, private: "secret" } },
      { status, headers: { "Retry-After": "1" } },
    ),
  );
  const response = await POST(request({ cursor: A }));
  expect(response.status).toBe(status);
  expect(await response.json()).toEqual({ code: expected, retry_cursor: B });
  expect(response.headers.get("retry-after")).toBe(
    expected === "feed_busy" ? "1" : null,
  );
});
it("keeps network failure generic and does not retry", async () => {
  fetcher.mockRejectedValue(new Error("private backend address"));
  await expect(
    startDiscoverFeed({ query: "Python", remote_only: true }),
  ).rejects.toMatchObject({ code: "feed_unavailable" });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it("routes start/advance/replay with one API request and no-store", async () => {
  expect(
    (await POST(request({ query: " Python ", remote_only: true }))).status,
  ).toBe(200);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    query: "Python",
    remote_only: true,
  });
  expect((await POST(request({ cursor: A }))).status).toBe(200);
  const response = await GET(
    new Request(`http://web.test/api/discover/feed?cursor=${A}`),
  );
  expect(await response.json()).toEqual(feedPage);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(fetcher).toHaveBeenCalledTimes(3);
});
it.each([
  { cursor: "bad" },
  { cursor: A, query: "override" },
  { query: "x", remote_only: false },
  { query: "x", remote_only: true, offset: 0 },
  { query: " ", remote_only: true },
  [],
  null,
])("rejects invalid POST %j before API", async (value) => {
  expect((await POST(request(value))).status).toBe(400);
  expect(fetcher).not.toHaveBeenCalled();
});
it.each([`cursor=${A}&cursor=${A}`, `cursor=${A}&offset=0`, `cursor=bad`, ""])(
  "rejects invalid GET %s before API",
  async (query) => {
    expect(
      (await GET(new Request(`http://web.test/api/discover/feed?${query}`)))
        .status,
    ).toBe(400);
    expect(fetcher).not.toHaveBeenCalled();
  },
);
it("rejects unsafe origin before mutation", async () => {
  const response = await POST(
    new Request("http://web.test/api/discover/feed", {
      method: "POST",
      headers: {
        origin: "http://evil.test",
        "content-type": "application/json",
      },
      body: JSON.stringify({ cursor: A }),
    }),
  );
  expect(response.status).toBe(403);
  expect(fetcher).not.toHaveBeenCalled();
});
