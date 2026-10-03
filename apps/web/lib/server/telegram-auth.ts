import "server-only";
import { randomBytes } from "node:crypto";
import { NextResponse } from "next/server";
import { authFetch, getCurrentUser, namedCookie, projectUser } from "./auth";
import { authError, checkRateLimit } from "./auth-handlers";
import { cookieConfig, setSession, TOKEN_PATTERN } from "./cookie";
import { requireMutation } from "./origin";
import { WebError } from "../errors";

const BINDING_PATTERN = /^[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}\.(login|link)$/;
function challengeConfig(token: string) {
  const config = cookieConfig();
  return {
    ...config,
    name: config.secure
      ? `__Host-job_hunter_telegram_${token}`
      : `job_hunter_telegram_dev_${token}`,
  };
}
// Cookie names are attempt-scoped: even a stale response can only delete its
// own binding, regardless of which cookies the browser received meanwhile.
function clear(response: NextResponse, token: string) {
  response.cookies.set({
    ...challengeConfig(token),
    value: "",
    maxAge: 0,
    expires: new Date(0),
  });
}
async function context(
  purpose: "login" | "link",
): Promise<Record<string, string>> {
  const state = await getCurrentUser();
  if (state.kind === "unavailable") throw new WebError("auth_unavailable", 503);
  if (purpose === "link") {
    if (state.kind !== "authenticated")
      throw new WebError("unauthenticated", 401);
    return { Authorization: `Bearer ${state.token}` };
  }
  if (state.kind === "authenticated")
    throw new WebError("TELEGRAM_CHALLENGE_INVALID", 400);
  return {};
}
async function payload(request: Request, creating: boolean) {
  requireMutation(request);
  const text = await request.text();
  if (text.length > 10000) throw new WebError("invalid_request", 400);
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new WebError("invalid_request", 400);
  }
  const allowed = creating ? ["purpose", "password"] : ["purpose", "token"];
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).some((key) => !allowed.includes(key)) ||
    !["login", "link"].includes(value.purpose)
  )
    throw new WebError("invalid_request", 400);
  if (creating) {
    if (
      (value.purpose === "link" && typeof value.password !== "string") ||
      (value.purpose === "login" && value.password !== undefined)
    )
      throw new WebError("invalid_request", 400);
  } else if (
    typeof value.token !== "string" ||
    !TOKEN_PATTERN.test(value.token)
  )
    throw new WebError("invalid_request", 400);
  return value as {
    purpose: "login" | "link";
    password?: string;
    token?: string;
  };
}
async function checked(response: Response) {
  if (response.ok) return response.json();
  checkRateLimit(response);
  let code;
  try {
    code = (await response.json())?.detail?.code;
  } catch {
    /* safe default */
  }
  const allowed = [
    "ACCOUNT_LINK_CONFLICT",
    "TELEGRAM_CHALLENGE_INVALID",
    "TELEGRAM_CHALLENGE_EXPIRED",
    "TELEGRAM_CHALLENGE_CANCELLED",
    "TELEGRAM_CHALLENGE_CONSUMED",
    "TELEGRAM_CHALLENGE_PENDING",
    "TELEGRAM_CHALLENGE_CONFLICT",
    "AUTH_INVALID_CREDENTIALS",
  ];
  if (allowed.includes(code)) throw new WebError(code, response.status);
  if (response.status === 401) throw new WebError("unauthenticated", 401);
  throw new WebError("auth_unavailable", 503);
}
export async function createTelegramChallenge(request: Request) {
  try {
    const input = await payload(request, true);
    const headers = await context(input.purpose);
    const binding = randomBytes(32).toString("base64url");
    const data = await checked(
      await authFetch("telegram/challenges", {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ ...input, binding }),
      }),
    );
    if (
      !TOKEN_PATTERN.test(data?.token ?? "") ||
      !/^[A-F0-9]{6}$/.test(data?.code ?? "") ||
      data?.status !== "pending"
    )
      throw new WebError("auth_unavailable", 503);
    const expires = new Date(data.expires_at);
    const now = Date.now();
    if (
      !Number.isFinite(expires.getTime()) ||
      expires.getTime() <= now ||
      expires.getTime() > now + 301000
    )
      throw new WebError("auth_unavailable", 503);
    const url = new URL(data.deep_link);
    if (
      url.protocol !== "https:" ||
      url.hostname !== "t.me" ||
      url.port ||
      url.username ||
      url.password ||
      !/^\/[A-Za-z0-9_]{5,32}$/.test(url.pathname) ||
      url.search !== `?start=auth_${data.token}` ||
      url.hash
    )
      throw new WebError("auth_unavailable", 503);
    const response = NextResponse.json(
      {
        token: data.token,
        deep_link: data.deep_link,
        code: data.code,
        expires_at: expires.toISOString(),
        status: "pending",
      },
      { headers: { "Cache-Control": "no-store" } },
    );
    response.cookies.set({
      ...challengeConfig(data.token),
      value: `${data.token}.${binding}.${input.purpose}`,
      expires,
      maxAge: Math.max(0, Math.floor((expires.getTime() - now) / 1000)),
    });
    return response;
  } catch (error) {
    return authError(error);
  }
}
export async function telegramChallenge(
  request: Request,
  operation: "status" | "complete",
) {
  let cleanupToken: string | undefined;
  try {
    const input = await payload(request, false);
    const cookie = await namedCookie(
      challengeConfig(input.token!).name,
      BINDING_PATTERN,
    );
    const [token, binding, purpose] = cookie.token?.split(".") ?? [];
    if (!binding || token !== input.token || purpose !== input.purpose)
      throw new WebError("TELEGRAM_CHALLENGE_INVALID", 400);
    cleanupToken = token;
    const headers = await context(input.purpose);
    const data = await checked(
      await authFetch(`telegram/${operation}`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ token, binding, purpose }),
      }),
    );
    if (operation === "status") {
      if (
        ![
          "pending",
          "approved",
          "expired",
          "cancelled",
          "conflict",
          "consumed",
        ].includes(data?.status)
      )
        throw new WebError("auth_unavailable", 503);
      const response = NextResponse.json(
        { status: data.status },
        { headers: { "Cache-Control": "no-store" } },
      );
      if (
        ["expired", "cancelled", "conflict", "consumed"].includes(data.status)
      )
        clear(response, token);
      return response;
    }
    const me = projectUser(data.me);
    const response = NextResponse.json(
      { ok: true, me },
      { headers: { "Cache-Control": "no-store" } },
    );
    if (purpose === "login") {
      if (
        !TOKEN_PATTERN.test(data.session_token ?? "") ||
        !Number.isFinite(Date.parse(data.expires_at)) ||
        Date.parse(data.expires_at) <= Date.now()
      )
        throw new WebError("auth_unavailable", 503);
      setSession(response, data.session_token, data.expires_at);
    } else if (data.ok !== true || !me.telegram_linked)
      throw new WebError("auth_unavailable", 503);
    clear(response, token);
    return response;
  } catch (error) {
    const response = authError(error);
    if (
      cleanupToken &&
      error instanceof WebError &&
      [
        "TELEGRAM_CHALLENGE_INVALID",
        "ACCOUNT_LINK_CONFLICT",
        "TELEGRAM_CHALLENGE_EXPIRED",
        "TELEGRAM_CHALLENGE_CANCELLED",
        "TELEGRAM_CHALLENGE_CONSUMED",
        "TELEGRAM_CHALLENGE_CONFLICT",
      ].includes(error.code)
    )
      clear(response, cleanupToken);
    return response;
  }
}

export async function cancelTelegramChallenge(request: Request) {
  try {
    const input = await payload(request, false);
    if (input.purpose !== "login") throw new WebError("invalid_request", 400);
    const cookie = await namedCookie(
      challengeConfig(input.token!).name,
      BINDING_PATTERN,
    );
    const [token, binding, purpose] = cookie.token?.split(".") ?? [];
    if (!binding || token !== input.token || purpose !== "login")
      throw new WebError("TELEGRAM_CHALLENGE_INVALID", 400);
    let confirmed = false;
    try {
      const data = await checked(
        await authFetch("telegram/cancel", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token, binding, purpose }),
          signal: AbortSignal.timeout(1500),
        }),
      );
      confirmed = data?.ok === true;
    } catch {
      // Local abandonment must succeed even if the API cannot revoke the proof.
      // With no binding or polling it cannot complete here and expires by TTL.
    }
    const response = NextResponse.json(
      { ok: true, cancellation_confirmed: confirmed },
      { headers: { "Cache-Control": "no-store" } },
    );
    clear(response, token);
    return response;
  } catch (error) {
    return authError(error);
  }
}
