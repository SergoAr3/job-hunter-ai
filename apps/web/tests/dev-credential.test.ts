// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { GET } from "../app/api/applications/route";
import { GET as profileGet } from "../app/api/profile/route";
import { getConfig } from "../lib/server/config";
const token = "web-tests-server-only-dev-token-123456";
beforeEach(() => {
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("AUTH_ROLLOUT_MODE", "legacy-development");
  vi.stubEnv("WEB_DEV_API_TOKEN", token);
  vi.stubEnv("WEB_DEV_USER_ID", "987");
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
it.each([
  ["APP_ENV", "production"],
  ["AUTH_ROLLOUT_MODE", "enforced"],
  ["WEB_DEV_API_TOKEN", ""],
  ["WEB_DEV_API_TOKEN", "short"],
  ["NODE_ENV", "production"],
  ["API_BASE_URL", "https://external.example"],
])("fails closed for %s=%s", (name, value) => {
  vi.stubEnv(name, value);
  expect(() => getConfig()).toThrow("configuration");
});
it.each([GET, profileGet])(
  "attaches only server dev credential and never reflects browser credentials",
  async (handler) => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(Response.json({ detail: "private" }, { status: 503 }));
    vi.stubGlobal("fetch", fetcher);
    const response = await handler(
      new Request("http://localhost/api/applications", {
        headers: {
          "X-Web-Dev-Api-Token": "attacker",
          "X-Bot-Service-Token": "bot-secret",
          Authorization: "Bearer attacker",
        },
      }),
    );
    expect(fetcher.mock.calls[0][0]).toContain("/users/987/");
    const headers = new Headers(fetcher.mock.calls[0][1].headers);
    expect(headers.get("X-Web-Dev-Api-Token")).toBe(token);
    expect(headers.has("X-Bot-Service-Token")).toBe(false);
    expect(headers.has("Authorization")).toBe(false);
    expect(await response.text()).not.toContain(token);
    expect(JSON.stringify([...response.headers])).not.toContain(token);
  },
);
