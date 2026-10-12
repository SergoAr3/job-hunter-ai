// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("../lib/server/auth", async () => ({
  getDomainIdentity: vi.fn((await import("./session-fixture")).sessionIdentity),
}));
import { getDomainIdentity } from "../lib/server/auth";
import { WebError } from "../lib/errors";
import { GET } from "../app/api/applications/[applicationId]/match/route";
import { matchFixture } from "./match-fixtures";

const url = "http://localhost:3100/api/applications/42/match";
const context = (applicationId = "42") => ({
  params: Promise.resolve({ applicationId }),
});

it.each(["job-match-v2.1", "job-match-v2.2"] as const)(
  "accepts the explicit matcher version %s through the API route",
  async (algorithm_version) => {
    const fixture = { ...matchFixture(), algorithm_version };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(fixture)));
    const response = await GET(new Request(url), context());
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(fixture);
  },
);
beforeEach(() => {
  vi.mocked(getDomainIdentity).mockResolvedValue({
    userId: "987",
    baseUrl: "http://127.0.0.1:8000",
    headers: { Authorization: `Bearer ${"T".repeat(43)}` },
    user: {
      email: null,
      display_name: null,
      profile_exists: true,
      telegram_linked: false,
      email_verified: true,
      created_at: "2026-10-10T00:00:00Z",
    },
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(getDomainIdentity).mockReset();
});

it("uses authenticated GET with no-store and projects nested allowlisted fields", async () => {
  const payload = {
    ...matchFixture(),
    user_id: 987,
    parsing_error: "private",
    input_state: { ...matchFixture().input_state, private_key: "secret" },
    components: {
      ...matchFixture().components,
      role: { ...matchFixture().components.role, debug: "private" },
    },
  };
  const fetcher = vi.fn().mockResolvedValue(Response.json(payload));
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(new Request(url), context());
  expect(response.status).toBe(200);
  expect(response.headers.get("Cache-Control")).toBe("no-store");
  expect(await response.json()).toEqual(matchFixture());
  expect(fetcher.mock.calls[0][0]).toBe(
    "http://127.0.0.1:8000/users/987/applications/42/match",
  );
  expect(fetcher.mock.calls[0][1]).toMatchObject({
    method: "GET",
    cache: "no-store",
    headers: { Authorization: `Bearer ${"T".repeat(43)}` },
  });
});
it("rejects invalid IDs and browser identity query before fetching", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  for (const id of ["0", "-1", "01", "../1", "9007199254740992"])
    expect((await GET(new Request(url), context(id))).status).toBe(404);
  expect((await GET(new Request(`${url}?user_id=1`), context())).status).toBe(
    400,
  );
  expect(fetcher).not.toHaveBeenCalled();
});
it("requires a session and never fetches as a fallback identity", async () => {
  vi.mocked(getDomainIdentity).mockRejectedValueOnce(
    new WebError("unauthenticated", 401),
  );
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(new Request(url), context());
  expect(response.status).toBe(401);
  expect(await response.json()).toEqual({ code: "unauthenticated" });
  expect(fetcher).not.toHaveBeenCalled();
});
it.each([
  [409, "PROFILE_REQUIRED"],
  [404, "APPLICATION_NOT_FOUND"],
])("preserves bounded API errors %s", async (status, code) => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ detail: { code, secret: "private" } }, { status }),
      ),
  );
  const response = await GET(new Request(url), context());
  expect(response.status).toBe(status);
  expect(await response.json()).toEqual({ code });
});
it("sanitizes timeout and infrastructure error", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockRejectedValueOnce(
        new DOMException("private upstream", "TimeoutError"),
      )
      .mockResolvedValueOnce(
        Response.json(
          { detail: { code: "SQL_SECRET", message: "private" } },
          { status: 500 },
        ),
      ),
  );
  for (let i = 0; i < 2; i++) {
    const response = await GET(new Request(url), context());
    expect(response.status).toBeGreaterThanOrEqual(500);
    expect(await response.json()).toEqual({ code: "api_unavailable" });
  }
});
it.each([
  { algorithm_version: "job-match-v99" },
  { score: false },
  { score: null },
  { verdict: "invented" },
  { application_id: 99 },
  { coverage: 101 },
  { confidence: "certain" },
  { components: {} },
  { strengths: [{ code: "SQL_SECRET", component: "role" }] },
  { explainability: { schema_version: 2, components: {} } },
  {
    input_state: {
      ...matchFixture().input_state,
      profile_updated_at: "not a time",
    },
  },
])("rejects malformed backend data safely: %s", async (changes) => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ ...matchFixture(), ...changes })),
  );
  const response = await GET(new Request(url), context());
  expect(response.status).toBe(502);
  expect(await response.json()).toEqual({ code: "api_unavailable" });
});
it("accepts a legacy response without explainability and distinguishes score zero/null", async () => {
  const legacy = {
    ...matchFixture(),
    explainability: undefined,
    score: 0,
    verdict: "low",
  };
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(Response.json(legacy))
      .mockResolvedValueOnce(
        Response.json({
          ...legacy,
          score: null,
          verdict: "insufficient_data",
          confidence: null,
          coverage: 20,
        }),
      ),
  );
  expect((await (await GET(new Request(url), context())).json()).score).toBe(0);
  expect(
    (await (await GET(new Request(url), context())).json()).score,
  ).toBeNull();
});
it("does not interpret PROFILE_REQUIRED on a server error as an empty profile", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { detail: { code: "PROFILE_REQUIRED" } },
          { status: 500 },
        ),
      ),
  );
  const response = await GET(new Request(url), context());
  expect(await response.json()).toEqual({ code: "api_unavailable" });
});
