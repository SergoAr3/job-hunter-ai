// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NextRequest, NextResponse } from "next/server";
vi.mock("server-only", () => ({}));
const incoming = vi.hoisted(() => ({ request: null as NextRequest | null }));
vi.mock("next/headers", () => ({
  headers: async () => incoming.request!.headers,
  cookies: async () => incoming.request!.cookies,
}));
vi.mock("next/navigation", () => ({
  redirect: (url: string) => {
    throw new Error(`REDIRECT:${url}`);
  },
}));
import { GET as profile, PUT as putProfile } from "../app/api/profile/route";
import { GET as experiences } from "../app/api/profile/work-experiences/route";
import { GET as facts } from "../app/api/profile/experience-facts/route";
import { GET as discover } from "../app/api/discover/route";
import { POST as save } from "../app/api/discover/save/route";
import { GET as applications } from "../app/api/applications/route";
import { GET as detail } from "../app/api/applications/[applicationId]/route";
import { GET as history } from "../app/api/applications/[applicationId]/status-history/route";
import { PUT as status } from "../app/api/applications/[applicationId]/status/route";
import { logoutHandler } from "../lib/server/auth-handlers";
import { pageAccess } from "../lib/server/page-access";
import Home from "../app/page";
import DashboardPage from "../app/(workspace)/dashboard/page";
import CVImportPage from "../app/(workspace)/profile/import/page";
import { AuthUnavailable } from "../components/auth-unavailable";

const token = "T".repeat(43),
  cookieName = "job_hunter_session_dev";
const me = {
  email: "b@example.com",
  email_verified: false,
  telegram_linked: false,
  display_name: "B",
  profile_exists: true,
  created_at: "2026-10-02T00:00:00Z",
};
const upstream = vi.fn();
const profileBody = {
  target_roles: ["Python Engineer"],
  skills: [],
  experience: "unknown",
  location: [],
  workplace_preference: "any",
  salary_min: null,
  salary_currency: null,
  salary_period: "unknown",
  languages: [],
};
const ctx = { params: Promise.resolve({ applicationId: "12" }) };
const handlers = [
  ["profile", "GET", undefined, (r: Request) => profile(r)],
  ["profile update", "PUT", profileBody, (r: Request) => putProfile(r)],
  ["experiences", "GET", undefined, (r: Request) => experiences(r)],
  ["facts", "GET", undefined, (r: Request) => facts(r)],
  ["discover?query=python", "GET", undefined, (r: Request) => discover(r)],
  [
    "discover/save",
    "POST",
    { source: "hh", source_scope: "ru", external_id: "12" },
    (r: Request) => save(r),
  ],
  ["applications", "GET", undefined, (r: Request) => applications(r)],
  ["applications/12", "GET", undefined, (r: Request) => detail(r, ctx)],
  [
    "applications/12/status-history",
    "GET",
    undefined,
    (r: Request) => history(r, ctx),
  ],
  [
    "applications/12/status",
    "PUT",
    { status: "saved" },
    (r: Request) => status(r, ctx),
  ],
] as const;
function request(path: string, cookie: string, method = "GET", body?: unknown) {
  const r = new NextRequest(`http://localhost:3100/api/${path}`, {
    method,
    headers: {
      Origin: "http://localhost:3100",
      "Content-Type": "application/json",
      Cookie: cookie,
      "X-Web-Dev-Api-Token": "ignored-old-secret",
      "X-Bot-Service-Token": "browser-cannot-select-bot",
      Authorization: "Bearer browser-cannot-select-identity",
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  incoming.request = r;
  return r;
}
beforeEach(() => {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("APP_ENV", "development");
  // Deliberately set removed settings. They must never grant an identity.
  vi.stubEnv("WEB_DEV_USER_ID", "987");
  vi.stubEnv("WEB_DEV_API_TOKEN", "ignored-old-secret");
  vi.stubEnv("AUTH_ROLLOUT_MODE", "legacy-development");
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "http://localhost:3100");
  upstream.mockReset();
  vi.stubGlobal("fetch", upstream);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it.each(
  handlers.flatMap((handler) =>
    (["absent", "malformed", "expired", "revoked", "unavailable"] as const).map(
      (state) => [handler, state] as const,
    ),
  ),
)(
  "%s session=%s never selects a default identity",
  async ([path, method, body, handler], state) => {
    upstream.mockResolvedValue(
      Response.json({}, { status: state === "unavailable" ? 503 : 401 }),
    );
    const cookie =
      state === "absent"
        ? ""
        : `${cookieName}=${state === "malformed" ? "bad" : token}`;
    const r = request(path, cookie, method, body);
    const result = await handler(r);
    expect(result.status).toBe(state === "unavailable" ? 503 : 401);
    expect(result.headers.has("set-cookie")).toBe(state !== "unavailable");
    if (state !== "unavailable")
      expect(result.headers.get("set-cookie")).toContain("Max-Age=0");
    expect(upstream).toHaveBeenCalledTimes(
      ["absent", "malformed"].includes(state) ? 0 : 1,
    );
    for (const [url, options] of upstream.mock.calls) {
      expect(url).toContain("/auth/internal/principal");
      expect(url).not.toContain("/users/");
      expect(new Headers(options.headers).get("Authorization")).toBe(
        `Bearer ${token}`,
      );
      expect(new Headers(options.headers).has("X-Web-Dev-Api-Token")).toBe(
        false,
      );
      expect(new Headers(options.headers).has("X-Bot-Service-Token")).toBe(
        false,
      );
    }
    expect(await result.text()).not.toContain("987");
  },
);
it.each([
  "/dashboard",
  "/profile",
  "/discover?q=python",
  "/applications",
  "/applications/12",
])(
  "protected %s requires login and safe next without any default",
  async (path) => {
    request("profile", "");
    await expect(pageAccess(path)).rejects.toThrow(
      `REDIRECT:/login?next=${encodeURIComponent(path)}`,
    );
    expect(upstream).not.toHaveBeenCalled();
  },
);
it("root chooses login, real workspace or unavailable", async () => {
  request("profile", "");
  await expect(Home()).rejects.toThrow("REDIRECT:/login");
  request("profile", `${cookieName}=${token}`);
  upstream.mockResolvedValue(Response.json({ user_id: 42, me }));
  await expect(Home()).rejects.toThrow("REDIRECT:/dashboard");
  upstream.mockResolvedValue(Response.json({}, { status: 503 }));
  const result = await Home();
  expect(result.type).toBe(AuthUnavailable);
});
it("logout clears cookie and every protected page stays logged out", async () => {
  upstream.mockResolvedValue(new Response(null, { status: 204 }));
  const result = await logoutHandler(
    request("auth/logout", `${cookieName}=${token}`, "POST", {}),
  );
  expect((result as NextResponse).cookies.get(cookieName)?.maxAge).toBe(0);
  expect(await result.json()).toEqual({ ok: true, revocation_confirmed: true });
  upstream.mockClear();
  request("profile", "");
  for (const path of ["/profile", "/discover", "/applications"])
    await expect(pageAccess(path)).rejects.toThrow(
      `REDIRECT:/login?next=${encodeURIComponent(path)}`,
    );
  expect((await profile(incoming.request!)).status).toBe(401);
  expect(upstream).not.toHaveBeenCalled();
});
it("outage preserves session and recovery resumes the same principal", async () => {
  request("profile", `${cookieName}=${token}`);
  upstream.mockResolvedValue(Response.json({}, { status: 503 }));
  const outage = await profile(incoming.request!);
  expect(outage.status).toBe(503);
  expect(outage.headers.has("set-cookie")).toBe(false);
  upstream.mockImplementation(async (url: string) =>
    url.endsWith("internal/principal")
      ? Response.json({ user_id: 42, me })
      : Response.json({ items: [], has_next: false }),
  );
  const recovered = await applications(
    request("applications", `${cookieName}=${token}`),
  );
  expect(recovered.status).toBe(200);
  expect(upstream.mock.calls.at(-1)![0]).toContain("/users/42/applications");
});

it.each(["absent", "malformed", "expired", "revoked", "unavailable"] as const)(
  "Dashboard session=%s uses the real session resolver before domain loading",
  async (state) => {
    const cookie =
      state === "absent"
        ? ""
        : `${cookieName}=${state === "malformed" ? "bad" : token}`;
    request("profile", cookie);
    upstream.mockResolvedValue(
      Response.json({}, { status: state === "unavailable" ? 503 : 401 }),
    );
    if (state === "unavailable") {
      expect((await DashboardPage()).type).toBe(AuthUnavailable);
    } else {
      await expect(DashboardPage()).rejects.toThrow(
        "REDIRECT:/login?next=%2Fdashboard",
      );
    }
    for (const [url] of upstream.mock.calls)
      expect(url).toContain("/auth/internal/principal");
    expect(upstream).toHaveBeenCalledTimes(
      ["absent", "malformed"].includes(state) ? 0 : 1,
    );
  },
);

it.each(["absent", "malformed", "expired", "revoked", "unavailable"] as const)(
  "CV Import session=%s preserves protected workspace semantics",
  async (state) => {
    request(
      "profile/import",
      state === "absent"
        ? ""
        : `${cookieName}=${state === "malformed" ? "bad" : token}`,
    );
    upstream.mockResolvedValue(
      Response.json({}, { status: state === "unavailable" ? 503 : 401 }),
    );
    if (state === "unavailable")
      expect((await CVImportPage()).type).toBe(AuthUnavailable);
    else
      await expect(CVImportPage()).rejects.toThrow(
        "REDIRECT:/login?next=%2Fprofile%2Fimport",
      );
    expect(upstream).toHaveBeenCalledTimes(
      ["absent", "malformed"].includes(state) ? 0 : 1,
    );
  },
);
