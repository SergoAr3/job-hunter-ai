// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("../lib/server/auth", async () => ({
  getDomainIdentity: vi.fn((await import("./session-fixture")).sessionIdentity),
}));
import { POST, GET } from "../app/api/profile/work-experiences/route";
import {
  PATCH,
  DELETE,
} from "../app/api/profile/work-experiences/[experienceId]/route";
const origin = "http://localhost:3100",
  url = origin + "/api/profile/work-experiences";
const context = { params: Promise.resolve({ experienceId: "42" }) };
const entry = {
  id: 42,
  company: "Acme",
  position: "Engineer",
  engagement_kind: "unknown",
  start_year: null,
  start_month: null,
  end_year: null,
  end_month: null,
  is_current: null,
  duration_months: null,
  user_profile_id: 999,
  internal: "secret",
};
function request(method: string, body: unknown, address = url, from = origin) {
  return new Request(address, {
    method,
    headers: { origin: from, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}
beforeEach(() => {
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("WEB_PUBLIC_ORIGIN", origin);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it("resource POST/PATCH/DELETE use session identity, canonical ID projection and no-store", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(entry))
    .mockResolvedValueOnce(Response.json({ ...entry, company: null }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetcher);
  const created = await POST(request("POST", { company: "Acme" }));
  expect(created.status).toBe(201);
  expect(created.headers.get("cache-control")).toBe("no-store");
  const result = await created.json();
  expect(result.id).toBe(42);
  expect(result).not.toHaveProperty("user_profile_id");
  expect(result).not.toHaveProperty("internal");
  expect(
    (await PATCH(request("PATCH", { company: null }), context)).status,
  ).toBe(200);
  expect(await (await DELETE(request("DELETE", {}), context)).json()).toEqual({
    ok: true,
  });
  expect(fetcher.mock.calls.map((c) => c[0])).toEqual([
    "http://127.0.0.1:8000/users/987/profile/work-experiences",
    "http://127.0.0.1:8000/users/987/profile/work-experiences/42",
    "http://127.0.0.1:8000/users/987/profile/work-experiences/42",
  ]);
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ company: null });
});
it.each([
  { user_id: 1 },
  { id: 42 },
  { source: "manual" },
  { engagement_kind: "full_time" },
  { start_year: true },
  { is_current: "true" },
])(
  "rejects ownership/unsupported fields and bad types before upstream %j",
  async (body) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    expect((await POST(request("POST", body))).status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  },
);
it("requires origin, rejects queries, bounds actual stream and validates ids", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect(
    (await POST(request("POST", {}, url, "https://evil.test"))).status,
  ).toBe(403);
  expect((await POST(request("POST", {}, url + "?user_id=1"))).status).toBe(
    400,
  );
  expect(
    (await POST(request("POST", { company: "x".repeat(9000) }))).status,
  ).toBe(413);
  expect(
    (
      await PATCH(request("PATCH", {}), {
        params: Promise.resolve({ experienceId: "../2" }),
      })
    ).status,
  ).toBe(404);
  expect(
    (await DELETE(request("DELETE", { company: "No" }), context)).status,
  ).toBe(400);
  expect(fetcher).not.toHaveBeenCalled();
});
it.each([
  [401, "unauthenticated"],
  [403, "work_forbidden"],
  [404, "work_not_found"],
  [409, "work_conflict"],
  [500, "work_unconfirmed"],
])("safe upstream %s mapping", async (status, code) => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ detail: "secret SQL" }, { status: status as number }),
      ),
  );
  expect(
    await (await PATCH(request("PATCH", { position: "New" }), context)).json(),
  ).toEqual({ code });
});
it("sanitizes validation, duplicates, limits, malformed responses and network uncertainty without retry", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json(
        {
          detail: [
            { loc: ["body", "position"], msg: "SECRET", input: "SECRET" },
          ],
        },
        { status: 422 },
      ),
    )
    .mockResolvedValueOnce(
      Response.json(
        { detail: { code: "DUPLICATE_WORK_EXPERIENCE" } },
        { status: 422 },
      ),
    )
    .mockResolvedValueOnce(
      Response.json(
        { detail: { code: "WORK_EXPERIENCE_LIMIT_REACHED" } },
        { status: 422 },
      ),
    )
    .mockResolvedValueOnce(Response.json({}))
    .mockRejectedValueOnce(new Error("secret"));
  vi.stubGlobal("fetch", fetcher);
  expect(
    await (await PATCH(request("PATCH", { position: "New" }), context)).json(),
  ).toEqual({
    code: "work_invalid",
    fieldErrors: { position: "Проверьте поле «Должность»." },
  });
  for (const code of [
    "work_duplicate",
    "work_limit",
    "work_unconfirmed",
    "work_unconfirmed",
  ])
    expect(
      await (await POST(request("POST", { company: "New" }))).json(),
    ).toEqual({ code });
  expect(fetcher).toHaveBeenCalledTimes(5);
});
it("read list exposes stable entry IDs without ownership or internal metadata", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ items: [entry] })),
  );
  const result = await (await GET(new Request(url))).json();
  expect(result.items[0].id).toBe(42);
  expect(result.items[0]).not.toHaveProperty("user_profile_id");
});
