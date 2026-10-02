// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("../lib/server/auth", async () => ({
  getDomainIdentity: vi.fn((await import("./session-fixture")).sessionIdentity),
}));
import { GET } from "../app/api/applications/[applicationId]/status-history/route";

const url = "http://localhost:3100/api/applications/42/status-history";
const context = (applicationId = "42") => ({
  params: Promise.resolve({ applicationId }),
});

beforeEach(() => {
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "http://localhost");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it("gets ordered history for the authenticated user and projects only public fields", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      items: [
        {
          status: "offer",
          occurred_at: "2026-09-30T14:42:00Z",
          user_id: 987,
        },
        {
          status: "applied",
          occurred_at: "2026-09-29T13:10:00Z",
        },
      ],
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(new Request(url), context());
  expect(response.status).toBe(200);
  expect(response.headers.get("Cache-Control")).toBe("no-store");
  expect(await response.json()).toEqual({
    items: [
      { status: "offer", occurred_at: "2026-09-30T14:42:00Z" },
      { status: "applied", occurred_at: "2026-09-29T13:10:00Z" },
    ],
  });
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][0]).toBe(
    "http://127.0.0.1:8000/users/987/applications/42/status-history",
  );
  expect(fetcher.mock.calls[0][1].method).toBe("GET");
  expect(fetcher.mock.calls[0][1].cache).toBe("no-store");
});

it("rejects browser user query and invalid application IDs before upstream", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect((await GET(new Request(`${url}?user_id=1`), context())).status).toBe(
    400,
  );
  for (const id of ["../1", "0", "999999999999999999999"]) {
    const response = await GET(new Request(url), context(id));
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ code: "APPLICATION_NOT_FOUND" });
  }
  expect(fetcher).not.toHaveBeenCalled();
});

it("propagates owned 404 safely and hides malformed upstream data", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json(
        { detail: { code: "APPLICATION_NOT_FOUND" } },
        { status: 404 },
      ),
    )
    .mockResolvedValueOnce(
      Response.json({
        items: [{ status: "offer", occurred_at: "2026-09-30T14:42:00" }],
      }),
    );
  vi.stubGlobal("fetch", fetcher);
  const missing = await GET(new Request(url), context());
  expect(missing.status).toBe(404);
  expect(await missing.json()).toEqual({ code: "APPLICATION_NOT_FOUND" });
  const malformed = await GET(new Request(url), context());
  expect(malformed.status).toBe(502);
  expect(await malformed.json()).toEqual({ code: "api_unavailable" });
});
