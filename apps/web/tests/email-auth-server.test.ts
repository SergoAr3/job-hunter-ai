// @vitest-environment node
import { beforeEach, afterEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { emailHandler, loginHandler } from "../lib/server/auth-handlers";
import { createTelegramChallenge } from "../lib/server/telegram-auth";
import config from "../next.config";
const upstream = vi.fn();
const token = "t".repeat(43);
function request(
  operation: string,
  input: unknown,
  origin = "http://localhost:3100",
) {
  return new Request(`http://localhost:3100/api/auth/${operation}`, {
    method: "POST",
    headers: { Origin: origin, "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}
beforeEach(() => {
  vi.stubGlobal("fetch", upstream);
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("NODE_ENV", "test");
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "");
  vi.stubEnv("API_BASE_URL", "http://localhost:8000");
});
afterEach(() => {
  upstream.mockReset();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it.each([
  "email/resend",
  "email/verify",
  "password/forgot",
  "password/reset",
] as const)(
  "%s forwards only proof/input and projects safe success",
  async (operation) => {
    const requesting = ["email/resend", "password/forgot"].includes(operation);
    const input = requesting
      ? { email: "user@example.com" }
      : operation === "password/reset"
        ? { token, password: "  Unicode пароль 🔒  " }
        : { token };
    upstream.mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          user_id: 4,
          token_digest: "private",
          password_hash: "private",
          session_token: "private",
        }),
        { status: requesting ? 202 : 200 },
      ),
    );
    const response = await emailHandler(request(operation, input), operation);
    expect(response.status).toBe(requesting ? 202 : 200);
    expect(await response.json()).toEqual({ ok: true });
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(upstream.mock.calls[0][0]).toBe(
      `http://localhost:8000/auth/${operation}`,
    );
    expect(JSON.parse(upstream.mock.calls[0][1].body)).toEqual(input);
    expect(
      new Headers(upstream.mock.calls[0][1].headers).has("X-Bot-Service-Token"),
    ).toBe(false);
    expect(
      new Headers(upstream.mock.calls[0][1].headers).has("Authorization"),
    ).toBe(false);
    const cookie = response.headers.get("Set-Cookie");
    if (operation === "password/reset")
      expect(cookie).toMatch(/job_hunter_session_dev=;.*Max-Age=0/);
    else expect(cookie).toBeNull();
  },
);
it.each([
  "email/resend",
  "email/verify",
  "password/forgot",
  "password/reset",
] as const)(
  "%s rejects foreign origin and extra fields before fetch",
  async (operation) => {
    expect(
      (
        await emailHandler(
          request(
            operation,
            { token, email: "user@example.com", password: "private" },
            "https://attacker.example",
          ),
          operation,
        )
      ).status,
    ).toBe(403);
    expect(
      (
        await emailHandler(
          request(operation, {
            token,
            email: "user@example.com",
            password: "private",
            user_id: 4,
          }),
          operation,
        )
      ).status,
    ).toBe(422);
    expect(upstream).not.toHaveBeenCalled();
  },
);
it.each(["EMAIL_TOKEN_INVALID", "EMAIL_TOKEN_USED", "EMAIL_TOKEN_EXPIRED"])(
  "projects safe token error %s",
  async (code) => {
    upstream.mockResolvedValue(
      new Response(
        JSON.stringify({ detail: { code, token_digest: "private" } }),
        { status: 400 },
      ),
    );
    const response = await emailHandler(
      request("email/verify", { token }),
      "email/verify",
    );
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ code });
    expect(response.headers.get("Set-Cookie")).toBeNull();
  },
);
it("verification-required login does not create or clear session", async () => {
  upstream.mockResolvedValue(
    new Response(
      JSON.stringify({
        detail: { code: "EMAIL_VERIFICATION_REQUIRED", user_id: 4 },
      }),
      { status: 403 },
    ),
  );
  const response = await loginHandler(
    request("login", { email: "user@example.com", password: "private" }),
  );
  expect(response.status).toBe(403);
  expect(await response.json()).toEqual({
    code: "EMAIL_VERIFICATION_REQUIRED",
  });
  expect(response.headers.get("Set-Cookie")).toBeNull();
});
it.each(["10", "-1", "private\nvalue", "9999999"])(
  "429 safe Retry-After %s",
  async (retry) => {
    // Invalid newline headers cannot be constructed, use a syntactically valid hostile value.
    const value = retry.replace("\n", " ");
    upstream.mockResolvedValue(
      new Response(
        JSON.stringify({ code: "rate_limited", private: "secret" }),
        { status: 429, headers: { "Retry-After": value } },
      ),
    );
    const response = await emailHandler(
      request("email/verify", { token }),
      "email/verify",
    );
    expect(response.status).toBe(429);
    expect(await response.json()).toEqual({ code: "rate_limited" });
    expect(response.headers.get("Retry-After")).toBe(
      retry === "10" ? "10" : null,
    );
  },
);
it("Telegram challenge creation preserves safe 429 semantics", async () => {
  upstream.mockResolvedValue(
    new Response('{"code":"rate_limited"}', {
      status: 429,
      headers: { "Retry-After": "600" },
    }),
  );
  const response = await createTelegramChallenge(
    request("telegram/challenges", { purpose: "login" }),
  );
  expect(response.status).toBe(429);
  expect(await response.json()).toEqual({ code: "rate_limited" });
  expect(response.headers.get("Retry-After")).toBe("600");
});
it("token pages use no-referrer policy", async () => {
  const headers = await config.headers!();
  for (const path of ["/verify-email", "/reset-password"])
    expect(
      headers.find((item) => item.source === path)?.headers,
    ).toContainEqual({ key: "Referrer-Policy", value: "no-referrer" });
});
