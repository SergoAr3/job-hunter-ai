// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("../lib/server/auth", () => ({
  getDomainIdentity: async () => ({
    userId: "42",
    baseUrl: "http://api",
    headers: { Authorization: "Bearer test-session" },
  }),
}));
import { getApplicationsSummary } from "../lib/server/api";
import { applicationStatuses } from "../lib/applications";
const counts = Object.fromEntries(applicationStatuses.map((s) => [s, 3]));
afterEach(() => vi.unstubAllGlobals());
it("requests owner summary with session and no-store; projects only public data", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      Response.json({ total: 24, status_counts: counts, user_id: 42 }),
    );
  vi.stubGlobal("fetch", fetcher);
  expect(await getApplicationsSummary()).toEqual({
    total: 24,
    status_counts: counts,
  });
  expect(fetcher).toHaveBeenCalledWith(
    "http://api/users/42/applications/summary",
    expect.objectContaining({
      method: "GET",
      cache: "no-store",
      headers: { Authorization: "Bearer test-session" },
    }),
  );
});
it.each([
  { total: 23, status_counts: counts },
  { total: 24, status_counts: { ...counts, saved: -1 } },
  { total: 24, status_counts: { ...counts, saved: 1.5 } },
  { total: 24, status_counts: { saved: 24 } },
  { total: 24, status_counts: { ...counts, fake: 0 } },
  {},
])("rejects malformed counts %j", async (body) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(body)));
  await expect(getApplicationsSummary()).rejects.toMatchObject({
    code: "api_unavailable",
  });
});
it.each([401, 503])("preserves HTTP %s semantics", async (status) => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({}, { status })),
  );
  await expect(getApplicationsSummary()).rejects.toMatchObject({
    code: status === 401 ? "unauthenticated" : "api_unavailable",
  });
});
