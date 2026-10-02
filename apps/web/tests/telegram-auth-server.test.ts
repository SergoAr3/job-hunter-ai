// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
vi.mock("server-only", () => ({}));
const incoming = vi.hoisted(() => ({
  request: null as import("next/server").NextRequest | null,
}));
vi.mock("next/headers", () => ({
  headers: async () => incoming.request!.headers,
  cookies: async () => incoming.request!.cookies,
}));
import {
  createTelegramChallenge,
  cancelTelegramChallenge,
  telegramChallenge,
} from "../lib/server/telegram-auth";
const token = "T".repeat(43),
  binding = "B".repeat(43),
  session = "S".repeat(43);
const me = {
  email: "b@example.com",
  email_verified: false,
  telegram_linked: true,
  display_name: "B",
  profile_exists: true,
  created_at: "2026-10-02T00:00:00Z",
};
const upstream = vi.fn();
function req(
  payload: unknown,
  cookie = "",
  origin: string | null = "http://localhost:3100",
) {
  const r = new NextRequest("http://spoofed.invalid/api/auth/telegram", {
    method: "POST",
    headers: {
      ...(origin ? { Origin: origin } : {}),
      Cookie: cookie,
      "Content-Type": "application/json",
      Host: "evil.invalid",
    },
    body: JSON.stringify(payload),
  });
  incoming.request = r;
  return r;
}
function challengeCookie(purpose = "login") {
  return `job_hunter_telegram_dev_${token}=${token}.${binding}.${purpose}`;
}
beforeEach(() => {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("AUTH_ROLLOUT_MODE", "legacy-development");
  vi.stubEnv("WEB_DEV_USER_ID", "987");
  vi.stubEnv("WEB_DEV_API_TOKEN", "never-browser-dev-credential");
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "http://localhost:3100");
  upstream.mockReset();
  vi.stubGlobal("fetch", upstream);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
it("sets separate browser binding cookie, projects challenge, never leaks binding or DB hash", async () => {
  upstream.mockResolvedValue(
    Response.json({
      token,
      deep_link: `https://t.me/identity_bot?start=auth_${token}`,
      code: "A1B2C3",
      status: "pending",
      expires_at: new Date(Date.now() + 300000).toISOString(),
      binding: "SECRET",
      token_hash: "HASH",
    }),
  );
  const response = await createTelegramChallenge(req({ purpose: "login" }));
  expect(response.status).toBe(200);
  const body = await response.json();
  expect(body.token).toBe(token);
  expect(body).not.toHaveProperty("binding");
  expect(body).not.toHaveProperty("token_hash");
  const sent = JSON.parse(upstream.mock.calls[0][1].body);
  expect(sent.binding).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(body).not.toHaveProperty("binding");
  const cookie = response.headers.get("set-cookie")!;
  expect(cookie).toContain(`job_hunter_telegram_dev_${token}=`);
  expect(cookie).toContain("HttpOnly");
  expect(cookie).toContain("SameSite=lax");
  expect(cookie).toContain("Path=/");
  expect(cookie).not.toContain("Domain=");
  expect(cookie).not.toContain("job_hunter_session_dev");
  expect(upstream.mock.calls[0][1].headers).not.toHaveProperty(
    "X-Web-Dev-Api-Token",
  );
});
it.each([
  "",
  `job_hunter_telegram_dev_${token}=%ZZ`,
  `${challengeCookie()}; ${challengeCookie()}`,
  challengeCookie("link"),
])(
  "wrong/missing/duplicate binding rejects before fetch: %s",
  async (cookie) => {
    const response = await telegramChallenge(
      req({ purpose: "login", token }, cookie),
      "complete",
    );
    expect(response.status).toBe(400);
    expect(upstream).not.toHaveBeenCalled();
  },
);
it("approved login uses same normal session cookie and safe browser DTO", async () => {
  upstream.mockResolvedValue(
    Response.json({
      session_token: session,
      expires_at: new Date(Date.now() + 3600000).toISOString(),
      me: { ...me, user_id: 42, telegram_id: 123 },
      token_hash: "HASH",
    }),
  );
  const response = await telegramChallenge(
    req({ purpose: "login", token }, challengeCookie()),
    "complete",
  );
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ ok: true, me });
  const cookie = response.headers.get("set-cookie")!;
  expect(cookie).toContain("job_hunter_session_dev=");
  expect(cookie).toContain(`job_hunter_telegram_dev_${token}=`);
  expect(cookie).toContain("Max-Age=0");
  const sent = JSON.parse(upstream.mock.calls[0][1].body);
  expect(sent).toEqual({ purpose: "login", token, binding });
});
it.each([{ session_token: "bad" }, { expires_at: "bad" }, { me: {} }])(
  "malformed completion never sets session cookie: %j",
  async (patch) => {
    upstream.mockResolvedValue(
      Response.json({
        session_token: session,
        expires_at: new Date(Date.now() + 3600000).toISOString(),
        me,
        ...patch,
      }),
    );
    const response = await telegramChallenge(
      req({ purpose: "login", token }, challengeCookie()),
      "complete",
    );
    expect(response.status).toBe(503);
    expect(response.headers.has("set-cookie")).toBe(false);
  },
);
it("link requires real session and preserves password/Bearer only server-side", async () => {
  expect(
    (
      await createTelegramChallenge(
        req({ purpose: "link", password: "  Unicode пароль 🔑  " }),
      )
    ).status,
  ).toBe(401);
  expect(upstream).not.toHaveBeenCalled();
  upstream.mockImplementation(async (url: string) =>
    url.endsWith("internal/principal")
      ? Response.json({ user_id: 42, me })
      : Response.json({
          token,
          deep_link: `https://t.me/identity_bot?start=auth_${token}`,
          code: "A1B2C3",
          status: "pending",
          expires_at: new Date(Date.now() + 300000).toISOString(),
        }),
  );
  const response = await createTelegramChallenge(
    req(
      { purpose: "link", password: "  Unicode пароль 🔑  " },
      `job_hunter_session_dev=${session}`,
    ),
  );
  expect(response.status).toBe(200);
  expect(upstream.mock.calls[1][1].headers.Authorization).toBe(
    `Bearer ${session}`,
  );
  expect(JSON.parse(upstream.mock.calls[1][1].body).password).toBe(
    "  Unicode пароль 🔑  ",
  );
  expect(await response.text()).not.toContain(session);
});
it("terminal conflict clears challenge, preserves account session and discloses no identity", async () => {
  upstream.mockImplementation(async (url: string) =>
    url.endsWith("internal/principal")
      ? Response.json({ user_id: 42, me })
      : Response.json(
          {
            detail: {
              code: "ACCOUNT_LINK_CONFLICT",
              email: "private@example.com",
              user_id: 7,
            },
          },
          { status: 409 },
        ),
  );
  const response = await telegramChallenge(
    req(
      { purpose: "link", token },
      `${challengeCookie("link")}; job_hunter_session_dev=${session}`,
    ),
    "complete",
  );
  expect(response.status).toBe(409);
  expect(await response.json()).toEqual({ code: "ACCOUNT_LINK_CONFLICT" });
  expect(response.headers.get("set-cookie")).toContain(
    `job_hunter_telegram_dev_${token}=`,
  );
  expect(response.headers.get("set-cookie")).not.toContain(
    "job_hunter_session_dev=",
  );
});
it.each([null, "null", "https://evil.example"])(
  "all Telegram browser mutations reject origin %s",
  async (origin) => {
    for (const operation of ["status", "complete"] as const)
      expect(
        (
          await telegramChallenge(
            req({ purpose: "login", token }, challengeCookie(), origin),
            operation,
          )
        ).status,
      ).toBe(403);
    expect(
      (await createTelegramChallenge(req({ purpose: "login" }, "", origin)))
        .status,
    ).toBe(403);
    expect(upstream).not.toHaveBeenCalled();
  },
);
it("status projects only state; unavailable preserves cookies", async () => {
  upstream.mockResolvedValue(
    Response.json({
      status: "approved",
      telegram_id: 123,
      user_id: 42,
      session_token: session,
    }),
  );
  expect(
    await (
      await telegramChallenge(
        req({ purpose: "login", token }, challengeCookie()),
        "status",
      )
    ).json(),
  ).toEqual({ status: "approved" });
  upstream.mockResolvedValue(Response.json({}, { status: 503 }));
  const response = await telegramChallenge(
    req({ purpose: "login", token }, challengeCookie()),
    "status",
  );
  expect(response.status).toBe(503);
  expect(response.headers.has("set-cookie")).toBe(false);
});

it.each(["success", "outage", "network", "consumed"])(
  "browser cancel %s clears binding only and projects no secrets",
  async (outcome) => {
    if (outcome === "network")
      upstream.mockRejectedValue(new Error("server-secret"));
    else
      upstream.mockResolvedValue(
        Response.json(
          outcome === "success"
            ? { ok: true, session_token: session, binding, user_id: 42 }
            : { detail: { code: "TELEGRAM_CHALLENGE_INVALID" } },
          {
            status:
              outcome === "success" ? 200 : outcome === "consumed" ? 409 : 503,
          },
        ),
      );
    const response = await cancelTelegramChallenge(
      req({ purpose: "login", token }, challengeCookie()),
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      ok: true,
      cancellation_confirmed: outcome === "success",
    });
    expect(response.headers.get("set-cookie")).toContain(
      `job_hunter_telegram_dev_${token}=`,
    );
    expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
    expect(response.headers.get("set-cookie")).not.toContain(
      "job_hunter_session_dev=",
    );
    expect(upstream.mock.calls[0][0]).toContain("/auth/telegram/cancel");
    expect(JSON.parse(upstream.mock.calls[0][1].body)).toEqual({
      purpose: "login",
      token,
      binding,
    });
    expect(upstream.mock.calls[0][1].headers).not.toHaveProperty(
      "Authorization",
    );
    expect(upstream.mock.calls[0][1].headers).not.toHaveProperty(
      "X-Bot-Service-Token",
    );
  },
);
it.each(["wrong-token", "missing", "duplicate", "link", "csrf"])(
  "cancel rejects %s without clearing another binding or calling API",
  async (variant) => {
    const response = await cancelTelegramChallenge(
      req(
        {
          purpose: variant === "link" ? "link" : "login",
          token: variant === "wrong-token" ? "W".repeat(43) : token,
        },
        variant === "missing"
          ? ""
          : variant === "duplicate"
            ? `${challengeCookie()}; ${challengeCookie()}`
            : challengeCookie(),
        variant === "csrf"
          ? "https://attacker.invalid"
          : "http://localhost:3100",
      ),
    );
    expect(response.status).toBe(variant === "csrf" ? 403 : 400);
    expect(upstream).not.toHaveBeenCalled();
    expect(response.headers.has("set-cookie")).toBe(false);
  },
);
