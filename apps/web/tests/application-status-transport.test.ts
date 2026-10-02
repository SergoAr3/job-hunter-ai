// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("../lib/server/auth", async () => ({
  getDomainIdentity: vi.fn((await import("./session-fixture")).sessionIdentity),
}));
import { PUT } from "../app/api/applications/[applicationId]/status/route";
import { setApplicationStatus } from "../lib/server/api";
import { saved } from "./fixtures";

const url = "http://localhost:3100/api/applications/42/status";
const context = (id = "42") => ({
  params: Promise.resolve({ applicationId: id }),
});
function request(
  body: unknown,
  origin = "http://localhost:3100",
  address = url,
) {
  return new Request(address, {
    method: "PUT",
    headers: {
      origin,
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
}

beforeEach(() => {
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "http://localhost");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it("sends only status to the authenticated user's owned Application", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      ...saved,
      application: { ...saved.application, status: "applied" },
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const response = await PUT(request({ status: "applied" }), context());
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ ok: true });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe(
    "http://127.0.0.1:8000/users/987/applications/42/status",
  );
  expect(fetcher.mock.calls[0][1].method).toBe("PUT");
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    status: "applied",
  });
});

it.each([
  [{ status: "applied", user_id: 1 }, 422],
  [{ status: "unknown" }, 422],
  [{}, 422],
  [{ status: null }, 422],
  [[{ status: "applied" }], 422],
])("rejects invalid payload %j before API", async (body, expected) => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const response = await PUT(request(body), context());
  expect(response.status).toBe(expected);
  expect(fetcher).not.toHaveBeenCalled();
});

it("rejects arbitrary user query, cross-origin calls and malformed application ID", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect(
    (
      await PUT(
        request(
          { status: "saved" },
          "http://localhost:3100",
          `${url}?user_id=1`,
        ),
        context(),
      )
    ).status,
  ).toBe(400);
  expect(
    (await PUT(request({ status: "saved" }, "https://evil.test"), context()))
      .status,
  ).toBe(403);
  expect(
    (await PUT(request({ status: "saved" }), context("../1"))).status,
  ).toBe(404);
  expect(fetcher).not.toHaveBeenCalled();
});

it("propagates owned 404 safely and normalizes upstream 422", async () => {
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
  const missing = await PUT(request({ status: "offer" }), context());
  expect(missing.status).toBe(404);
  expect(await missing.json()).toEqual({ code: "APPLICATION_NOT_FOUND" });
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { detail: [{ msg: "internal validation" }] },
          { status: 422 },
        ),
      ),
  );
  const invalid = await PUT(request({ status: "offer" }), context());
  expect(invalid.status).toBe(422);
  expect(await invalid.json()).toEqual({ code: "status_invalid" });
});

it("marks timeout, 5xx and malformed successful response as ambiguous", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new Error("connection reset")),
  );
  const network = await PUT(request({ status: "offer" }), context());
  expect(await network.json()).toEqual({ code: "ambiguous_status" });
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(Response.json({ detail: "debug" }, { status: 504 })),
  );
  const server = await PUT(request({ status: "offer" }), context());
  expect(await server.json()).toEqual({ code: "ambiguous_status" });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({})));
  await expect(setApplicationStatus("42", "offer")).rejects.toThrow(
    "ambiguous_status",
  );
});

it("keeps configuration failure separate from an attempted mutation", async () => {
  vi.stubEnv("API_BASE_URL", "");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const response = await PUT(request({ status: "offer" }), context());
  expect(response.status).toBe(503);
  expect(await response.json()).toEqual({ code: "configuration" });
  expect(fetcher).not.toHaveBeenCalled();
});
