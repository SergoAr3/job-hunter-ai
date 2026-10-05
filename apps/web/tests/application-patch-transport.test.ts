// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("../lib/server/auth", async () => ({
  getDomainIdentity: vi.fn((await import("./session-fixture")).sessionIdentity),
}));
import { PATCH } from "../app/api/applications/[applicationId]/route";
import { saved } from "./fixtures";

const url = "http://localhost:3100/api/applications/42";
const context = (id = "42") => ({
  params: Promise.resolve({ applicationId: id }),
});
function request(
  body: unknown,
  origin = "http://localhost:3100",
  address = url,
) {
  return new Request(address, {
    method: "PATCH",
    headers: { origin, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}
beforeEach(() => {
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "http://localhost:3100");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it("uses authenticated identity, forwards partial fields/null and projects the response", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      ...saved,
      application: { ...saved.application, user_id: 987, note: null },
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const response = await PATCH(request({ note: null }), context());
  expect(response.status).toBe(200);
  expect(response.headers.get("Cache-Control")).toBe("no-store");
  expect((await response.json()).application).not.toHaveProperty("user_id");
  expect(fetcher.mock.calls[0][0]).toBe(
    "http://127.0.0.1:8000/users/987/applications/42",
  );
  expect(fetcher.mock.calls[0][1].method).toBe("PATCH");
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ note: null });
});

it.each([
  { user_id: 1 },
  { notes: "alias" },
  { next_action_due_on: "2026-10-10" },
  { status: "applied" },
  { note: 42 },
  { next_action: [] },
  [],
])("rejects %j before API", async (body) => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect((await PATCH(request(body), context())).status).toBe(422);
  expect(fetcher).not.toHaveBeenCalled();
});

it("rejects cross-origin, query authority, malformed JSON and ID", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect(
    (await PATCH(request({ note: "x" }, "https://evil.test"), context()))
      .status,
  ).toBe(403);
  expect(
    (await PATCH(request({}, undefined, `${url}?user_id=1`), context())).status,
  ).toBe(400);
  expect((await PATCH(request({}), context("../1"))).status).toBe(404);
  expect(
    (
      await PATCH(
        new Request(url, {
          method: "PATCH",
          headers: {
            origin: "http://localhost:3100",
            "content-type": "application/json",
          },
          body: "{",
        }),
        context(),
      )
    ).status,
  ).toBe(400);
  expect(fetcher).not.toHaveBeenCalled();
});

it("hides internal validation details and preserves owned not-found", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { detail: { code: "APPLICATION_INVALID" } },
          { status: 422 },
        ),
      ),
  );
  expect(await (await PATCH(request({ note: "x" }), context())).json()).toEqual(
    { code: "application_invalid" },
  );
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { detail: { code: "APPLICATION_NOT_FOUND" } },
          { status: 404 },
        ),
      ),
  );
  expect((await PATCH(request({}), context())).status).toBe(404);
});

it.each(["network", "server", "malformed", "wrong-id"])(
  "treats %s as ambiguous without retry",
  async (mode) => {
    const fetcher = vi.fn();
    if (mode === "network") fetcher.mockRejectedValue(new Error("lost"));
    else
      fetcher.mockResolvedValue(
        mode === "server"
          ? Response.json({}, { status: 503 })
          : mode === "wrong-id"
            ? Response.json({
                ...saved,
                application: { ...saved.application, id: 99 },
              })
            : Response.json({}),
      );
    vi.stubGlobal("fetch", fetcher);
    expect(
      await (await PATCH(request({ note: "x" }), context())).json(),
    ).toEqual({ code: "ambiguous_application" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);

it("keeps configuration failure separate", async () => {
  vi.stubEnv("API_BASE_URL", "");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect(await (await PATCH(request({}), context())).json()).toEqual({
    code: "configuration",
  });
  expect(fetcher).not.toHaveBeenCalled();
});
