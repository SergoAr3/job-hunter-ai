// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
vi.mock("server-only", () => ({}));
const { contexts } = await vi.hoisted(async () => {
  const { AsyncLocalStorage } = await import("node:async_hooks");
  return {
    contexts: new AsyncLocalStorage<{
      request: import("next/server").NextRequest;
      cache: Map<() => unknown, unknown>;
    }>(),
  };
});
vi.mock("next/headers", () => ({
  headers: async () => contexts.getStore()!.request.headers,
  // Actual NextRequest parser: decode failures are dropped, duplicate names collapse.
  cookies: async () => contexts.getStore()!.request.cookies,
}));
vi.mock("next/navigation", () => ({
  redirect: (url: string) => {
    throw new Error(`REDIRECT:${url}`);
  },
}));
vi.mock("react", async () => {
  const { createRequire } = await import("node:module");
  const { dirname, join } = await import("node:path");
  const require = createRequire(import.meta.url);
  const react = require(
    join(dirname(require.resolve("react")), "react.react-server.js"),
  );
  // Installed React.cache, with a deterministic render dispatcher supplying a
  // fresh cache root per async request context. The resolver is never mocked.
  react.__SERVER_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE.A = {
    getCacheForType(factory: () => unknown) {
      const cache = contexts.getStore()!.cache;
      if (!cache.has(factory)) cache.set(factory, factory());
      return cache.get(factory);
    },
  };
  return { cache: react.cache };
});
import { getCurrentUser, getDomainIdentity } from "../lib/server/auth";
import { pageAccess } from "../lib/server/page-access";
import { GET as profile, PUT as putProfile } from "../app/api/profile/route";
import { GET as applications } from "../app/api/applications/route";
const name = "job_hunter_session_dev";
const tokenA = "A".repeat(43);
const tokenB = "B".repeat(43);
const origin = "http://localhost:3100";
const me = {
  email: "a@example.com",
  email_verified: false,
  telegram_linked: false,
  display_name: "A",
  profile_exists: true,
  created_at: "2026-10-02T00:00:00Z",
};
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
function request(cookie: string, method = "GET", body?: unknown) {
  return new NextRequest(`${origin}/api/profile`, {
    method,
    headers: { cookie, origin, "content-type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}
function within<T>(req: NextRequest, run: () => T): T {
  return contexts.run({ request: req, cache: new Map() }, run);
}
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
beforeEach(() => {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
  vi.stubEnv("WEB_PUBLIC_ORIGIN", origin);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
it.each(["", `${name}_extra=${tokenA}`, `prefix_${name}=${tokenA}`])(
  "an absent exact cookie never selects an identity: %s",
  async (cookie) => {
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);
    await expect(
      within(request(cookie), getDomainIdentity),
    ).rejects.toMatchObject({ status: 401 });
    expect(upstream).not.toHaveBeenCalled();
    await expect(
      within(request(cookie), getDomainIdentity),
    ).rejects.toMatchObject({ status: 401 });
    vi.stubEnv("NODE_ENV", "production");
    await expect(
      within(request(cookie), getDomainIdentity),
    ).rejects.toMatchObject({ status: 401 });
  },
);
it.each([
  `${name}=${tokenA}`,
  `other=hello; ${name}=${tokenA}; ${name}_extra=bad; last=value`,
  `${name}=%41${tokenA.slice(1)}`,
])("one decoded valid session selects its own identity: %s", async (cookie) => {
  const upstream = vi
    .fn()
    .mockResolvedValue(Response.json({ user_id: 42, me }));
  vi.stubGlobal("fetch", upstream);
  expect(await within(request(cookie), getDomainIdentity)).toMatchObject({
    userId: "42",
    headers: { Authorization: `Bearer ${tokenA}` },
  });
  expect(upstream).toHaveBeenCalledTimes(1);
  expect(upstream.mock.calls[0][0]).toBe(
    "http://127.0.0.1:8000/auth/internal/principal",
  );
});
it.each([
  `${name}=%ZZ`,
  `${name}=bad%`,
  `${name}=%E0%A4%A`,
  `${name}=%C0%AF`,
  `${name}=`,
  name,
  `${name}=bad`,
  `${name}=${tokenA}; ${name}=${tokenB}`,
  `${name}=${tokenA}; ${name}=${tokenA}`,
  `${name}=%ZZ; ${name}=${tokenA}`,
])(
  "raw malformed/duplicate cookie blocks every domain transport: %s",
  async (cookie) => {
    const upstream = vi.fn();
    vi.stubGlobal("fetch", upstream);
    await within(request(cookie), async () => {
      await expect(getDomainIdentity()).rejects.toMatchObject({
        code: "unauthenticated",
        status: 401,
      });
      await expect(pageAccess("/applications?q=R%26D")).rejects.toThrow(
        "REDIRECT:/login?next=%2Fapplications%3Fq%3DR%2526D",
      );
      for (const handler of [profile, applications]) {
        const response = await handler(new Request(`${origin}/api/profile`));
        expect(response.status).toBe(401);
        expect(await response.json()).toEqual({ code: "unauthenticated" });
        expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
      }
    });
    expect(upstream).not.toHaveBeenCalled();
  },
);
it("a backend-rejected real token never enables fallback", async () => {
  const upstream = vi
    .fn()
    .mockResolvedValue(new Response(null, { status: 401 }));
  vi.stubGlobal("fetch", upstream);
  await within(request(`${name}=${tokenA}`), async () => {
    expect(await getCurrentUser()).toEqual({
      kind: "unauthenticated",
      present: true,
    });
    await expect(getDomainIdentity()).rejects.toMatchObject({ status: 401 });
  });
  expect(upstream).toHaveBeenCalledTimes(1);
});
it("isolates interleaved real A/B reads, mutations and render cache across requests", async () => {
  const principalsStarted = deferred();
  const releaseA = deferred();
  const releaseB = deferred();
  const bDomainStarted = deferred();
  let principalCalls = 0;
  const profiles = new Map(
    [42, 43].map((id) => [
      id,
      {
        ...payload,
        target_roles: [`User ${id}`],
        created_at: me.created_at,
        updated_at: me.created_at,
      },
    ]),
  );
  const traffic: { id: number; bearer: string; method: string }[] = [];
  const upstream = vi.fn(async (url: string, init: RequestInit) => {
    const headers = new Headers(init.headers);
    const bearer = headers.get("Authorization")!;
    const id =
      bearer === `Bearer ${tokenA}`
        ? 42
        : bearer === `Bearer ${tokenB}`
          ? 43
          : 0;
    expect(id).not.toBe(0);
    expect(headers.has("X-Web-Dev-Api-Token")).toBe(false);
    expect(headers.has("X-Bot-Service-Token")).toBe(false);
    expect(init.cache).toBe("no-store");
    if (url.endsWith("/auth/internal/principal")) {
      principalCalls++;
      if (principalCalls === 2) principalsStarted.resolve();
      await (id === 42 ? releaseA.promise : releaseB.promise);
      return Response.json({
        user_id: id,
        me: { ...me, display_name: String(id) },
      });
    }
    expect(new URL(url).pathname).toMatch(new RegExp(`^/users/${id}/`));
    const method = init.method ?? "GET";
    traffic.push({ id, bearer, method });
    if (id === 43) bDomainStarted.resolve();
    if (new URL(url).pathname.endsWith("/profile")) {
      if (method === "PUT") {
        const other = id === 42 ? 43 : 42;
        const before = structuredClone(profiles.get(other));
        profiles.set(id, {
          ...profiles.get(id)!,
          ...JSON.parse(init.body as string),
        });
        expect(profiles.get(other)).toEqual(before);
      }
      return Response.json(profiles.get(id));
    }
    return Response.json({
      has_next: false,
      items: [
        {
          app_id: id,
          status: "saved",
          created_at: me.created_at,
          title: `Application ${id}`,
          company: null,
          location: null,
          workplace_type: "remote",
          parsing_status: "parsed",
          ai_enrichment_status: "pending",
        },
      ],
    });
  });
  vi.stubGlobal("fetch", upstream);
  const flow = (id: number, token: string) =>
    within(request(`${name}=${token}`), async () => {
      const first = getCurrentUser();
      expect(getCurrentUser()).toBe(first); // In-flight memoization in this render.
      expect(await first).toMatchObject({
        kind: "authenticated",
        userId: String(id),
      });
      const [p, a] = await Promise.all([
        profile(new Request(`${origin}/api/profile`)),
        applications(new Request(`${origin}/api/applications`)),
      ]);
      expect((await p.json()).target_roles).toEqual([`User ${id}`]);
      expect(
        (await a.json()).items.map((item: { app_id: number }) => item.app_id),
      ).toEqual([id]);
      const mutation = request(`${name}=${token}`, "PUT", {
        ...payload,
        target_roles: [`Updated ${id}`],
      });
      expect((await putProfile(mutation)).status).toBe(200);
      expect(
        (await (await profile(new Request(`${origin}/api/profile`))).json())
          .target_roles,
      ).toEqual([`Updated ${id}`]);
      expect(getCurrentUser()).toBe(first);
      const browser = JSON.stringify(
        await (await profile(new Request(`${origin}/api/profile`))).json(),
      );
      expect(browser).not.toContain(token);
      expect(browser).not.toContain("user_id");
    });
  const a = flow(42, tokenA);
  const b = flow(43, tokenB);
  await principalsStarted.promise;
  releaseB.resolve();
  await bDomainStarted.promise; // B resolves while A is still suspended.
  releaseA.resolve();
  await Promise.all([a, b]);
  expect(principalCalls).toBe(2);
  expect(profiles.get(42)!.target_roles).toEqual(["Updated 42"]);
  expect(profiles.get(43)!.target_roles).toEqual(["Updated 43"]);
  for (const id of [42, 43])
    expect(
      traffic.filter((call) => call.id === id && call.method === "PUT"),
    ).toHaveLength(1);
  // Fresh render must perform a fresh principal lookup after both have finished.
  await within(request(`${name}=${tokenB}`), async () => {
    expect(await getCurrentUser()).toMatchObject({
      kind: "authenticated",
      userId: "43",
    });
  });
  expect(principalCalls).toBe(3);
});
