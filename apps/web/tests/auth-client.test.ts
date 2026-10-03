// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});
it("domain expiry redirects once, preserving safe next", async () => {
  const assign = vi.fn();
  vi.stubGlobal("window", {
    location: { pathname: "/discover", search: "?q=python", assign },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ code: "unauthenticated" }, { status: 401 }),
    ),
  );
  const { webRequest } = await import("../lib/client");
  await Promise.allSettled([
    webRequest("/api/discover"),
    webRequest("/api/profile"),
  ]);
  expect(assign).toHaveBeenCalledExactlyOnceWith(
    "/login?next=%2Fdiscover%3Fq%3Dpython",
  );
});
it.each([
  ["/profile", "/api/profile", 503, "auth_unavailable"],
  ["/login", "/api/profile", 401, "unauthenticated"],
  ["/register", "/api/profile", 401, "unauthenticated"],
  ["/profile", "/api/auth/me", 401, "unauthenticated"],
  ["/login", "/api/auth/login", 401, "auth_invalid_credentials"],
] as const)(
  "no auth redirect loop or redirect on outage: %s %s %s",
  async (pathname, path, status, code) => {
    const assign = vi.fn();
    vi.stubGlobal("window", { location: { pathname, search: "", assign } });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ code }, { status })),
    );
    const { webRequest } = await import("../lib/client");
    await expect(webRequest(path)).rejects.toMatchObject({ code });
    expect(assign).not.toHaveBeenCalled();
  },
);
it("passes sanitized auth field errors to the form", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json(
        {
          code: "auth_invalid",
          fieldErrors: { email: "Проверьте введённые данные." },
        },
        { status: 422 },
      ),
    ),
  );
  const { webRequest } = await import("../lib/client");
  await expect(webRequest("/api/auth/register")).rejects.toMatchObject({
    fieldErrors: { email: "Проверьте введённые данные." },
  });
});

it.each(["login", "register", "logout"])(
  "browser network failure on %s reports auth outage, never Save or redirect",
  async (action) => {
    const assign = vi.fn();
    vi.stubGlobal("window", {
      location: { pathname: "/login", search: "", assign },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("connection lost")),
    );
    const { webRequest } = await import("../lib/client");
    await expect(
      webRequest(`/api/auth/${action}`, { method: "POST" }),
    ).rejects.toMatchObject({ code: "auth_unavailable", status: 503 });
    expect(assign).not.toHaveBeenCalled();
  },
);
