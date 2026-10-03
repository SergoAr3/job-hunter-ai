// @vitest-environment node
import { NextRequest, NextResponse } from "next/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const incoming = vi.hoisted(() => ({ request: null as NextRequest | null }));
vi.mock("next/headers", () => ({
  headers: async () => incoming.request!.headers,
  cookies: async () => incoming.request!.cookies,
}));
import {
  createTelegramChallenge,
  telegramChallenge,
  cancelTelegramChallenge,
} from "../lib/server/telegram-auth";
import { clearSession } from "../lib/server/cookie";

const A = "A".repeat(43),
  B = "B".repeat(43),
  SESSION = "S".repeat(43);
const me = {
  email: "owner@example.com",
  email_verified: false,
  telegram_linked: true,
  display_name: "Owner",
  profile_exists: true,
  created_at: "2026-10-02T00:00:00Z",
};
const upstream = vi.fn();
const cases = [
  "cancelled",
  "expired",
  "conflict",
  "consumed",
  "invalid-error",
  "conflict-error",
  "expired-error",
  "cancelled-error",
  "consumed-error",
  "complete",
  "cancel",
  "cancel-outage",
] as const;
type Case = (typeof cases)[number];
type Purpose = "login" | "link";
class BrowserCookies {
  values = new Map<string, string>();
  apply(response: NextResponse) {
    for (const cookie of response.cookies.getAll()) {
      if (cookie.maxAge === 0) this.values.delete(cookie.name);
      else this.values.set(cookie.name, cookie.value);
    }
  }
  header() {
    return [...this.values].map(([k, v]) => `${k}=${v}`).join("; ");
  }
}
function name(token: string) {
  return `job_hunter_telegram_dev_${token}`;
}
function invoke(
  jar: BrowserCookies,
  input: object,
  operation: "create" | "status" | "complete" | "cancel",
) {
  const request = new NextRequest("http://localhost:3100/api/auth/telegram", {
    method: "POST",
    headers: {
      Origin: "http://localhost:3100",
      "Content-Type": "application/json",
      Cookie: jar.header(),
    },
    body: JSON.stringify(input),
  });
  incoming.request = request;
  return operation === "create"
    ? createTelegramChallenge(request)
    : operation === "cancel"
      ? cancelTelegramChallenge(request)
      : telegramChallenge(request, operation);
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function challenge(token: string) {
  return Response.json({
    token,
    deep_link: `https://t.me/identity_bot?start=auth_${token}`,
    code: "123ABC",
    status: "pending",
    expires_at: new Date(Date.now() + 300000).toISOString(),
  });
}
function complete(session = "L".repeat(43)) {
  return Response.json({
    ok: true,
    me,
    session_token: session,
    expires_at: new Date(Date.now() + 3600000).toISOString(),
  });
}
function terminalResponse(kind: Case) {
  if (kind === "complete") return complete();
  if (kind === "cancel") return Response.json({ ok: true });
  if (kind === "cancel-outage") return Response.json({}, { status: 503 });
  if (kind.endsWith("-error"))
    return Response.json(
      {
        detail: {
          code:
            kind === "conflict-error"
              ? "ACCOUNT_LINK_CONFLICT"
              : `TELEGRAM_CHALLENGE_${kind.replace("-error", "").toUpperCase()}`,
        },
      },
      { status: kind === "invalid-error" ? 400 : 409 },
    );
  return Response.json({ status: kind });
}
beforeEach(() => {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "http://localhost:3100");
  upstream.mockReset();
  vi.stubGlobal("fetch", upstream);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it.each(
  (["login", "link"] as const).flatMap((purpose) =>
    cases
      .filter(
        (kind) =>
          purpose === "login" || !["cancel", "cancel-outage"].includes(kind),
      )
      .map((kind) => [purpose, kind] as const),
  ),
)(
  "late %s A %s cleans only A; B retains binding and can complete",
  async (purpose: Purpose, kind: Case) => {
    const jar = new BrowserCookies();
    if (purpose === "link") jar.values.set("job_hunter_session_dev", SESSION);
    const proofs = new Map<string, string>();
    const started = deferred<void>(),
      delayed = deferred<Response>();
    let count = 0;
    upstream.mockImplementation(async (url: string, init: RequestInit) => {
      if (url.endsWith("internal/principal"))
        return Response.json({ user_id: 42, me });
      const body = JSON.parse(init.body as string);
      if (url.endsWith("telegram/challenges")) {
        const token = count++ === 0 ? A : B;
        proofs.set(token, body.binding);
        return challenge(token);
      }
      expect(body.binding).toBe(proofs.get(body.token));
      expect(body.purpose).toBe(purpose);
      if (body.token === A) {
        started.resolve();
        return delayed.promise;
      }
      return url.endsWith("telegram/status")
        ? Response.json({ status: "approved" })
        : complete("N".repeat(43));
    });
    const input = {
      purpose,
      ...(purpose === "link"
        ? { password: "password for reauthentication" }
        : {}),
    };
    const first = await invoke(jar, input, "create");
    jar.apply(first);
    expect(first.status).toBe(200);
    const operation =
      kind === "complete"
        ? "complete"
        : ["cancel", "cancel-outage"].includes(kind)
          ? "cancel"
          : "status";
    const pending = invoke(jar, { purpose, token: A }, operation);
    // The barrier proves A has read its cookie snapshot before B is created.
    await started.promise;
    const second = await invoke(jar, input, "create");
    jar.apply(second);
    expect(second.status).toBe(200);
    expect(jar.values.has(name(A))).toBe(true);
    const bindingB = jar.values.get(name(B));
    delayed.resolve(terminalResponse(kind));
    const late = await pending;
    jar.apply(late);
    expect(late.cookies.get(name(A))?.maxAge).toBe(0);
    expect(late.cookies.get(name(B))).toBeUndefined();
    expect(jar.values.has(name(A))).toBe(false);
    expect(jar.values.get(name(B))).toBe(bindingB);
    const publicBody = JSON.stringify(await late.json());
    expect(publicBody).not.toContain(proofs.get(A)!);
    expect(publicBody).not.toContain(proofs.get(B)!);
    if (kind === "complete" && purpose === "login") {
      // Session cookies remain shared by tabs. Existing login rules reject an
      // authenticated browser; logout before continuing the surviving B proof.
      expect(jar.values.has("job_hunter_session_dev")).toBe(true);
      const logout = NextResponse.json({ ok: true });
      clearSession(logout);
      jar.apply(logout);
      expect(jar.values.get(name(B))).toBe(bindingB);
    } else if (purpose === "link")
      expect(jar.values.get("job_hunter_session_dev")).toBe(SESSION);
    const status = await invoke(jar, { purpose, token: B }, "status");
    expect(status.status).toBe(200);
    expect(await status.json()).toEqual({ status: "approved" });
    const result = await invoke(jar, { purpose, token: B }, "complete");
    expect(result.status).toBe(200);
    jar.apply(result);
    expect(jar.values.has(name(B))).toBe(false);
    expect(jar.values.get("job_hunter_session_dev")).toBe(
      purpose === "login" ? "N".repeat(43) : SESSION,
    );
  },
);

it("B terminal first preserves A pending binding and A request handling", async () => {
  const jar = new BrowserCookies(),
    proofs = new Map<string, string>();
  let count = 0;
  let bCancelled = false;
  upstream.mockImplementation(async (url: string, init: RequestInit) => {
    const body = JSON.parse(init.body as string);
    if (url.endsWith("telegram/challenges")) {
      const token = count++ === 0 ? A : B;
      proofs.set(token, body.binding);
      return challenge(token);
    }
    expect(body.binding).toBe(proofs.get(body.token));
    return Response.json({
      status: body.token === B && bCancelled ? "cancelled" : "pending",
    });
  });
  jar.apply(await invoke(jar, { purpose: "login" }, "create"));
  jar.apply(await invoke(jar, { purpose: "login" }, "create"));
  for (const token of [A, B]) {
    const response = await invoke(jar, { purpose: "login", token }, "status");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      status: "pending",
    });
  }
  bCancelled = true;
  const terminalB = await invoke(jar, { purpose: "login", token: B }, "status");
  expect(await terminalB.json()).toEqual({ status: "cancelled" });
  jar.apply(terminalB);
  expect(jar.values.has(name(B))).toBe(false);
  expect(jar.values.has(name(A))).toBe(true);
  expect(
    await (await invoke(jar, { purpose: "login", token: A }, "status")).json(),
  ).toEqual({ status: "pending" });
});

it("B binding, legacy shared cookie or token alone cannot authenticate A", async () => {
  for (const cookie of [
    "",
    `${name(B)}=${B}.${"X".repeat(43)}.login`,
    `job_hunter_telegram_dev=${A}.${"X".repeat(43)}.login`,
    `${name(A)}=${B}.${"X".repeat(43)}.login`,
  ]) {
    const jar = new BrowserCookies();
    if (cookie) {
      const [key, value] = cookie.split("=");
      jar.values.set(key, value);
    }
    for (const operation of ["status", "complete", "cancel"] as const) {
      const response = await invoke(
        jar,
        { purpose: "login", token: A },
        operation,
      );
      expect(response.status).toBe(400);
      expect(response.headers.has("set-cookie")).toBe(false);
    }
  }
  expect(upstream).not.toHaveBeenCalled();
});

it("production attempt cookie retains Secure HttpOnly __Host- and Path=/ semantics", async () => {
  vi.stubEnv("APP_ENV", "production");
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "https://app.example.com");
  upstream.mockResolvedValue(challenge(A));
  const request = new NextRequest("https://app.example.com/api/auth/telegram", {
    method: "POST",
    headers: {
      Origin: "https://app.example.com",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ purpose: "login" }),
  });
  incoming.request = request;
  const response = await createTelegramChallenge(request);
  expect(response.status).toBe(200);
  const cookie = response.cookies.get(`__Host-job_hunter_telegram_${A}`)!;
  expect(cookie).toMatchObject({
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
  });
  expect(cookie.domain).toBeUndefined();
  expect(JSON.stringify(await response.json())).not.toContain(cookie.value);
});
