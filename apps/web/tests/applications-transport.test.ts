// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { GET } from "../app/api/applications/route";
import { listApplications } from "../lib/server/api";
import {
  applicationsUrl,
  parseApplicationsState,
  safeApplicationsReturn,
} from "../lib/applications";

const item = {
  app_id: 42,
  job_id: 91,
  user_id: 987,
  status: "interview",
  created_at: "2026-09-30T10:00:00Z",
  title: "Python developer",
  company: "Company",
  location: "Москва",
  workplace_type: "remote",
  parsing_status: "success",
  ai_enrichment_status: "success",
};

beforeEach(() => {
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "http://localhost");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("AUTH_ROLLOUT_MODE", "legacy-development");
  vi.stubEnv("WEB_DEV_API_TOKEN", "web-tests-server-only-dev-token-123456");
  vi.stubEnv("WEB_DEV_USER_ID", "987");
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it("uses one list request for the configured user and projects list fields", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(Response.json({ items: [item], has_next: true }));
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(
    new Request(
      "http://localhost/api/applications?q=Python&status=interview&sort=oldest&offset=5&limit=5",
    ),
  );
  expect(response.status).toBe(200);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toContain("/users/987/applications?limit=5");
  expect(fetcher.mock.calls[0][0]).toContain("q=Python");
  expect(fetcher.mock.calls[0][0]).toContain("offset=5");
  expect(await response.json()).toEqual({
    has_next: true,
    items: [
      {
        app_id: 42,
        status: "interview",
        created_at: item.created_at,
        title: item.title,
        company: item.company,
        location: item.location,
        workplace_type: "remote",
        parsing_status: "success",
        ai_enrichment_status: "success",
      },
    ],
  });
});

it.each([
  "user_id=1",
  "limit=6",
  "status=other",
  "sort=random",
  "offset=-1",
  "offset=1",
  "offset=10005",
  "offset=9007199254740990",
  "offset=9007199254740993",
  `q=${"x".repeat(101)}`,
  "q=a&q=b",
])("rejects unsupported list query %s before reaching API", async (query) => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(
    new Request(`http://localhost/api/applications?${query}`),
  );
  expect(response.status).toBe(400);
  expect(await response.json()).toEqual({ code: "invalid_request" });
  expect(fetcher).not.toHaveBeenCalled();
});

it("hides raw API errors and invalid response data", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(Response.json({ detail: "secret" }, { status: 500 })),
  );
  expect(
    (await GET(new Request("http://localhost/api/applications"))).status,
  ).toBe(500);
  expect(
    await (await GET(new Request("http://localhost/api/applications"))).json(),
  ).toEqual({ code: "api_unavailable" });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ items: [{}], has_next: false })),
  );
  await expect(
    listApplications(parseApplicationsState(new URLSearchParams())),
  ).rejects.toThrow("api_unavailable");
});

it("parses and canonicalizes list URLs and rejects unsafe return paths", () => {
  const state = parseApplicationsState(
    new URLSearchParams("q=%20Python%20&status=offer&sort=oldest&offset=10"),
  );
  expect(applicationsUrl(state)).toBe(
    "/applications?q=Python&status=offer&sort=oldest&offset=10",
  );
  expect(
    parseApplicationsState(
      new URLSearchParams("status=bad&sort=bad&offset=-4"),
    ),
  ).toEqual({ q: "", status: null, sort: "newest", offset: 0 });
  for (const offset of ["1", "NaN", "9007199254740990", "10005"]) {
    expect(
      parseApplicationsState(new URLSearchParams(`offset=${offset}`)).offset,
    ).toBe(0);
  }
  expect(parseApplicationsState(new URLSearchParams("offset=5")).offset).toBe(
    5,
  );
  expect(
    parseApplicationsState(new URLSearchParams("offset=10000")).offset,
  ).toBe(10000);
  expect(
    safeApplicationsReturn(
      "/applications?q=Python&status=offer&sort=oldest&offset=10",
    ),
  ).toBe(applicationsUrl(state));
  for (const unsafe of [
    "https://evil.test",
    "//evil.test",
    "/applications/42",
    "/applications?next=https://evil.test",
    "/applications#frag",
    "/applications?status=offer&status=saved",
  ]) {
    expect(safeApplicationsReturn(unsafe)).toBe("/applications");
  }
  expect(safeApplicationsReturn(undefined)).toBe("/applications");
});
