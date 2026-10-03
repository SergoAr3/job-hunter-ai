// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const jar = vi.hoisted(() => ({
  values: [] as { name: string; value: string }[],
}));
vi.mock("next/headers", () => ({
  cookies: async () => ({ getAll: () => jar.values }),
  headers: async () =>
    new Headers({
      cookie: jar.values
        .map(({ name, value }) => `${name}=${value}`)
        .join("; "),
    }),
}));
vi.mock("next/navigation", () => ({
  redirect: (url: string) => {
    throw new Error(`REDIRECT:${url}`);
  },
}));
import {
  registerHandler,
  loginHandler,
  logoutHandler,
  meHandler,
} from "../lib/server/auth-handlers";
import { getCurrentUser, getDomainIdentity } from "../lib/server/auth";
import { pageAccess } from "../lib/server/page-access";
import { requireMutation } from "../lib/server/origin";
import { GET as profile, PUT as putProfile } from "../app/api/profile/route";
import { GET as applications } from "../app/api/applications/route";
import { GET as experiences } from "../app/api/profile/work-experiences/route";
import { GET as facts } from "../app/api/profile/experience-facts/route";
import { GET as discover } from "../app/api/discover/route";
import { POST as save } from "../app/api/discover/save/route";
import { GET as detail } from "../app/api/applications/[applicationId]/route";
import { GET as history } from "../app/api/applications/[applicationId]/status-history/route";
import { PUT as status } from "../app/api/applications/[applicationId]/status/route";
const token = "T".repeat(43);
const me = {
  email: "b@example.com",
  email_verified: false,
  telegram_linked: false,
  display_name: "B",
  profile_exists: false,
  created_at: "2026-10-02T00:00:00Z",
};
const upstream = vi.fn();
const body = { email: "b@example.com", password: "  Unicode пароль 🔑  " };
function request(
  path = "/api/auth/login",
  origin: string | null = "http://localhost:3100",
  payload: unknown = body,
) {
  return new Request(`http://spoofed.invalid${path}`, {
    method: "POST",
    headers: {
      ...(origin ? { Origin: origin } : {}),
      "Content-Type": "application/json",
      Host: "spoofed.invalid",
      "X-Forwarded-Host": "spoofed.invalid",
    },
    body: JSON.stringify(payload),
  });
}
function session() {
  jar.values = [{ name: "job_hunter_session_dev", value: token }];
}
beforeEach(() => {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "http://localhost:3100");
  jar.values = [];
  upstream.mockReset();
  vi.stubGlobal("fetch", upstream);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
it("register preserves generic 202 and exact Unicode/spaced password without creating a session", async () => {
  upstream.mockResolvedValue(
    Response.json({ detail: "generic" }, { status: 202 }),
  );
  const result = await registerHandler(request());
  expect(result.status).toBe(202);
  expect(await result.json()).toEqual({ ok: true });
  expect(result.headers.has("set-cookie")).toBe(false);
  expect(JSON.parse(upstream.mock.calls[0][1].body)).toEqual(body);
});
it.each([false, true])(
  "login keeps raw token/extra internal fields out of JSON; production=%s",
  async (production) => {
    if (production) {
      vi.stubEnv("NODE_ENV", "production");
      vi.stubEnv("WEB_PUBLIC_ORIGIN", "https://app.example.com");
    }
    upstream.mockResolvedValue(
      Response.json({
        session_token: token,
        expires_at: new Date(Date.now() + 3600000).toISOString(),
        me: { ...me, user_id: 42, password_hash: "secret" },
      }),
    );
    const result = await loginHandler(
      request(undefined, production ? "https://app.example.com" : undefined),
    );
    expect(result.status).toBe(200);
    expect(await result.json()).toEqual({ ok: true, me });
    expect(result.headers.get("cache-control")).toBe("no-store");
    const cookie = result.headers.get("set-cookie")!;
    expect(cookie).toContain(
      production ? "__Host-job_hunter_session=" : "job_hunter_session_dev=",
    );
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=lax");
    expect(cookie).toContain("Path=/");
    expect(cookie).not.toContain("Domain=");
    expect(cookie.includes("Secure")).toBe(production);
    expect(Number(cookie.match(/Max-Age=(\d+)/)?.[1])).toBeLessThanOrEqual(
      3600,
    );
  },
);
it("login rejects malformed upstream token, past expiry and malformed safe user", async () => {
  for (const patch of [
    { session_token: "bad" },
    { expires_at: "2020-01-01" },
    { me: {} },
  ]) {
    upstream.mockResolvedValue(
      Response.json({
        session_token: token,
        expires_at: new Date(Date.now() + 3600000).toISOString(),
        me,
        ...patch,
      }),
    );
    const result = await loginHandler(request());
    expect(result.status).toBe(503);
    expect(result.headers.has("set-cookie")).toBe(false);
  }
});
it("credential errors are generic and validation never echoes backend input/password", async () => {
  upstream.mockResolvedValue(
    Response.json({ secret: body.password }, { status: 401 }),
  );
  expect(await (await loginHandler(request())).json()).toEqual({
    code: "auth_invalid_credentials",
  });
  upstream.mockResolvedValue(
    Response.json(
      {
        detail: [
          {
            loc: ["body", "password"],
            input: body.password,
            msg: body.password,
          },
        ],
      },
      { status: 422 },
    ),
  );
  const result = await loginHandler(request());
  expect(result.status).toBe(422);
  expect(await result.text()).not.toContain(body.password);
});
it.each([null, "null", "https://evil.example", "http://spoofed.invalid"])(
  "rejects untrusted mutation origin %s before fetch even with cookie",
  async (origin) => {
    session();
    for (const handler of [
      registerHandler,
      loginHandler,
      logoutHandler,
      save,
    ]) {
      expect((await handler(request(undefined, origin))).status).toBe(403);
    }
    expect(upstream).not.toHaveBeenCalled();
  },
);
it("rejects cross-site Fetch Metadata and non-JSON; explicit local origins ignore spoofed Host", () => {
  expect(() => requireMutation(request())).not.toThrow();
  const cross = request();
  cross.headers.set("sec-fetch-site", "cross-site");
  expect(() => requireMutation(cross)).toThrow();
  const nonJson = request();
  nonJson.headers.set("Content-Type", "text/plain");
  expect(() => requireMutation(nonJson)).toThrow();
});
it.each([204, 503])(
  "logout clears local cookie even when backend returns %s",
  async (status) => {
    session();
    upstream.mockResolvedValue(new Response(null, { status }));
    const result = await logoutHandler(request("/api/auth/logout"));
    expect(await result.json()).toEqual({
      ok: true,
      revocation_confirmed: status === 204,
    });
    expect(result.headers.get("set-cookie")).toContain("Max-Age=0");
    expect(upstream.mock.calls[0][1].headers.Authorization).toBe(
      `Bearer ${token}`,
    );
  },
);
it("network outage on logout also clears cookie", async () => {
  session();
  upstream.mockRejectedValue(new Error("private backend failure"));
  const result = await logoutHandler(request());
  expect(await result.json()).toEqual({
    ok: true,
    revocation_confirmed: false,
  });
  expect(result.headers.get("set-cookie")).toContain("Max-Age=0");
});
it("safe me has neither ID nor token and never uses dev fallback", async () => {
  expect((await meHandler()).status).toBe(401);
  expect(upstream).not.toHaveBeenCalled();
  session();
  upstream.mockResolvedValue(
    Response.json({
      user_id: 42,
      me: { ...me, token, password_hash: "private" },
    }),
  );
  const result = await meHandler();
  expect(await result.json()).toEqual(me);
  expect(result.headers.get("cache-control")).toBe("no-store");
});
it.each([401, 503])(
  "backend %s remains distinct: only unauthenticated clears cookie",
  async (status) => {
    session();
    upstream.mockImplementation(async () => Response.json({}, { status }));
    const result = await meHandler();
    expect(result.status).toBe(status);
    expect(result.headers.has("set-cookie")).toBe(status === 401);
    await expect(getDomainIdentity()).rejects.toMatchObject({ status });
  },
);
it("malformed and duplicate cookies never select an identity", async () => {
  for (const values of [
    [{ name: "job_hunter_session_dev", value: "bad" }],
    [
      { name: "job_hunter_session_dev", value: token },
      { name: "job_hunter_session_dev", value: token },
    ],
  ]) {
    jar.values = values;
    await expect(getDomainIdentity()).rejects.toMatchObject({
      code: "unauthenticated",
    });
  }
  expect(upstream).not.toHaveBeenCalled();
});
it("no cookie never selects an identity", async () => {
  await expect(getDomainIdentity()).rejects.toMatchObject({ status: 401 });
});
it.each([
  "/profile",
  "/discover?q=python",
  "/applications",
  "/applications/123",
])(
  "server protection redirects missing session with safe next %s",
  async (path) => {
    await expect(pageAccess(path)).rejects.toThrow(
      `REDIRECT:/login?next=${encodeURIComponent(path)}`,
    );
  },
);
it("protected page preserves authenticated identity; outage renders unavailable instead of redirect", async () => {
  session();
  upstream.mockImplementation(async () => Response.json({ user_id: 42, me }));
  expect((await pageAccess("/profile"))?.userId).toBe("42");
  upstream.mockImplementation(async () => Response.json({}, { status: 503 }));
  expect(await pageAccess("/profile")).toBeNull();
});
it("malformed principal and network failure are unavailable, never dev fallback", async () => {
  session();
  upstream.mockResolvedValue(Response.json({ user_id: "42", me }));
  expect((await getCurrentUser()).kind).toBe("unavailable");
  upstream.mockRejectedValue(new Error("secret"));
  expect((await getCurrentUser()).kind).toBe("unavailable");
});
const ctx = { params: Promise.resolve({ applicationId: "12" }) };
it.each([
  ["profile", () => profile(new Request("http://localhost/api/profile"))],
  [
    "experiences",
    () =>
      experiences(new Request("http://localhost/api/profile/work-experiences")),
  ],
  [
    "facts",
    () => facts(new Request("http://localhost/api/profile/experience-facts")),
  ],
  [
    "discover",
    () => discover(new Request("http://localhost/api/discover?query=python")),
  ],
  [
    "save",
    () =>
      save(
        request("/api/discover/save", undefined, {
          source: "hh",
          source_scope: "am",
          external_id: "12",
        }),
      ),
  ],
  [
    "applications",
    () => applications(new Request("http://localhost/api/applications")),
  ],
  [
    "detail",
    () => detail(new Request("http://localhost/api/applications/12"), ctx),
  ],
  [
    "history",
    () =>
      history(
        new Request("http://localhost/api/applications/12/status-history"),
        ctx,
      ),
  ],
  [
    "status",
    () =>
      status(
        request("/api/applications/12/status", undefined, { status: "saved" }),
        ctx,
      ),
  ],
] as const)(
  "%s resolves real session B=42 over dev A=987 and clears only backend 401",
  async (_name, handler) => {
    session();
    upstream.mockImplementation(async (url: string) =>
      url.endsWith("/auth/internal/principal")
        ? Response.json({ user_id: 42, me })
        : Response.json({}, { status: 401 }),
    );
    const result = await handler();
    expect(result.status).toBe(401);
    expect(result.headers.get("set-cookie")).toContain("Max-Age=0");
    expect(upstream).toHaveBeenCalledTimes(2);
    const [url, options] = upstream.mock.calls[1];
    expect(url).toContain("/users/42/");
    expect(url).not.toContain("987");
    const headers = new Headers(options.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${token}`);
    expect(headers.has("X-Web-Dev-Api-Token")).toBe(false);
    expect(headers.has("X-Bot-Service-Token")).toBe(false);
  },
);

it("new Email-only profile can be created under real B without touching dev A", async () => {
  session();
  const payload = {
    target_roles: ["Python Engineer"],
    skills: ["Python"],
    experience: "middle",
    location: ["Yerevan"],
    workplace_preference: "remote",
    salary_min: "2500",
    salary_currency: "USD",
    salary_period: "month",
    languages: [],
  };
  upstream.mockImplementation(async (url: string, options: RequestInit) => {
    if (url.endsWith("/auth/internal/principal"))
      return Response.json({ user_id: 42, me });
    return options.method === "PUT"
      ? Response.json({
          ...payload,
          created_at: me.created_at,
          updated_at: me.created_at,
        })
      : Response.json({}, { status: 404 });
  });
  const missing = await profile(new Request("http://localhost/api/profile"));
  expect(await missing.json()).toEqual({ code: "profile_missing" });
  const result = await putProfile(request("/api/profile", undefined, payload));
  expect(await result.json()).toEqual({ ok: true });
  const [url, options] = upstream.mock.calls[3];
  expect(url).toContain("/users/42/profile");
  expect(options.headers.Authorization).toBe(`Bearer ${token}`);
});
it("empty upstream 401 still clears cookie; domain 503 never clears cookie", async () => {
  session();
  for (const status of [401, 503]) {
    upstream.mockImplementation(async (url: string) =>
      url.endsWith("/auth/internal/principal")
        ? Response.json({ user_id: 42, me })
        : new Response(null, { status }),
    );
    for (const handler of [profile, applications]) {
      const result = await handler(new Request("http://localhost/api/profile"));
      expect(result.headers.has("set-cookie")).toBe(status === 401);
    }
  }
});
