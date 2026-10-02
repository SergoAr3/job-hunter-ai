// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { getConfig } from "../lib/server/config";
import { getApplication, publicDetail, saveJob } from "../lib/server/api";
import { GET } from "../app/api/discover/route";
import { POST } from "../app/api/discover/save/route";
import { errorMessage, WebError } from "../lib/errors";
import { saved } from "./fixtures";
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
it("points make dev configuration errors to the root .env", () => {
  expect(errorMessage(new WebError("configuration"))).toContain(
    "Для make dev укажите корректный WEB_DEV_USER_ID в корневом .env",
  );
});
it.each([undefined, "", "0", "-1", "1.5", "abc", "9007199254740993"])(
  "rejects missing/invalid configured ID %s without a fallback",
  (value) => {
    vi.stubEnv("WEB_DEV_USER_ID", value);
    expect(() => getConfig()).toThrow("configuration");
  },
);
it("returns configuration error from transport before calling upstream", async () => {
  vi.stubEnv("WEB_DEV_USER_ID", "");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const result = await GET(
    new Request("http://localhost/api/discover?query=Python"),
  );
  expect(result.status).toBe(503);
  expect(await result.json()).toEqual({ code: "configuration" });
  expect(fetcher).not.toHaveBeenCalled();
});
it("refuses arbitrary user ID parameters", async () => {
  const result = await GET(
    new Request("http://localhost/api/discover?query=Python&user_id=1"),
  );
  expect(result.status).toBe(400);
});
it("strips internal IDs from detail responses", () => {
  const data = {
    ...saved,
    application: { ...saved.application, user_id: 987, job_id: 99 },
    job: { ...saved.job, id: 99 },
  };
  const result = publicDetail(data);
  expect(result.application).not.toHaveProperty("user_id");
  expect(JSON.stringify(result)).not.toContain("987");
});
it("routes through configured user and forwards only identity on Save", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(saved));
  vi.stubGlobal("fetch", fetcher);
  const identity = { source: "trudvsem", source_scope: "c", external_id: "v" };
  await saveJob(identity);
  expect(fetcher.mock.calls[0][0]).toBe(
    "http://127.0.0.1:8000/users/987/discover/jobs/save",
  );
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(identity);
});
it("rejects extra Save fields and cross-origin writes", async () => {
  const body = JSON.stringify({
    source: "trudvsem",
    source_scope: "c",
    external_id: "v",
    user_id: 1,
  });
  const result = await POST(
    new Request("http://localhost/api/discover/save", {
      method: "POST",
      headers: {
        origin: "http://localhost",
        "content-type": "application/json",
      },
      body,
    }),
  );
  expect(result.status).toBe(400);
  const cross = await POST(
    new Request("http://localhost/api/discover/save", {
      method: "POST",
      headers: { origin: "https://other.test" },
      body,
    }),
  );
  expect(cross.status).toBe(403);
});
it("does not expose backend debug errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ detail: "secret database debug" }, { status: 500 }),
      ),
  );
  const response = await GET(
    new Request("http://localhost/api/discover?query=Python"),
  );
  expect(await response.json()).toEqual({ code: "api_unavailable" });
});
it("maps missing Application without leaking ownership details", async () => {
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
  await expect(getApplication("42")).rejects.toThrow("APPLICATION_NOT_FOUND");
});
it("treats malformed successful Save as ambiguous", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({})));
  await expect(
    saveJob({ source: "trudvsem", source_scope: "c", external_id: "v" }),
  ).rejects.toThrow("ambiguous_save");
});
it("accepts explicit local origin without trusting Host or forwarded-host", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(saved));
  vi.stubGlobal("fetch", fetcher);
  const response = await POST(
    new Request("http://localhost:3100/api/discover/save", {
      method: "POST",
      headers: {
        host: "127.0.0.1:3100",
        origin: "http://127.0.0.1:3100",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        source: "trudvsem",
        source_scope: "c",
        external_id: "v",
      }),
    }),
  );
  expect(response.status).toBe(200);
  const forbidden = await POST(
    new Request("http://localhost:3100/api/discover/save", {
      method: "POST",
      headers: {
        host: "127.0.0.1:3100",
        origin: "https://evil.test",
        "x-forwarded-host": "evil.test",
      },
    }),
  );
  expect(forbidden.status).toBe(403);
});
