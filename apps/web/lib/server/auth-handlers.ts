import "server-only";
import { NextResponse } from "next/server";
import { WebError } from "../errors";
import { authFetch, getCurrentUser, projectUser, sessionCookie } from "./auth";
import { clearSession, setSession, TOKEN_PATTERN } from "./cookie";
import { requireMutation } from "./origin";

export function authError(error: unknown) {
  const safe =
    error instanceof WebError ? error : new WebError("auth_unavailable", 503);
  const response = NextResponse.json(
    {
      code: safe.code,
      ...(safe.fieldErrors ? { fieldErrors: safe.fieldErrors } : {}),
    },
    { status: safe.status, headers: { "Cache-Control": "no-store" } },
  );
  if (safe.code === "unauthenticated") clearSession(response);
  if (safe.status === 429 && safe.retryAfter)
    response.headers.set("Retry-After", safe.retryAfter);
  return response;
}
async function credentials(request: Request, register: boolean) {
  requireMutation(request);
  const text = await request.text();
  if (text.length > 10000) throw new WebError("auth_invalid", 422);
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new WebError("auth_invalid", 422);
  }
  const fields = register
    ? ["email", "password", "display_name"]
    : ["email", "password"];
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).some((key) => !fields.includes(key)) ||
    typeof value.email !== "string" ||
    typeof value.password !== "string" ||
    (register &&
      value.display_name != null &&
      typeof value.display_name !== "string")
  )
    throw new WebError("auth_invalid", 422);
  return value;
}
export async function checked(response: Response) {
  if (response.ok) return;
  checkRateLimit(response);
  if ([400, 403].includes(response.status)) {
    let code;
    try {
      code = (await response.json())?.detail?.code;
    } catch {
      /* safe default */
    }
    const allowed =
      response.status === 403
        ? ["EMAIL_VERIFICATION_REQUIRED"]
        : ["EMAIL_TOKEN_INVALID", "EMAIL_TOKEN_EXPIRED", "EMAIL_TOKEN_USED"];
    if (allowed.includes(code)) throw new WebError(code, response.status);
  }
  if (response.status === 401)
    throw new WebError("auth_invalid_credentials", 401);
  if (response.status === 422) {
    // Never echo backend input/message/ctx; only known field locations.
    const errors: Record<string, string> = {};
    try {
      const body = await response.json();
      if (Array.isArray(body?.detail))
        for (const item of body.detail) {
          const field = Array.isArray(item?.loc)
            ? item.loc.find(
                (x: unknown) =>
                  typeof x === "string" &&
                  ["email", "password", "display_name"].includes(x),
              )
            : null;
          if (field)
            errors[field] =
              field === "password"
                ? "Пароль должен содержать от 15 до 128 символов."
                : "Проверьте введённые данные.";
        }
    } catch {
      /* sanitized validation fallback */
    }
    throw new WebError("auth_invalid", 422, errors);
  }
  throw new WebError("auth_unavailable", 503);
}
export async function registerHandler(request: Request) {
  try {
    const payload = await credentials(request, true);
    const response = await authFetch("register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await checked(response);
    if (response.status !== 202) throw new WebError("auth_unavailable", 503);
    return NextResponse.json(
      { ok: true },
      { status: 202, headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return authError(error);
  }
}
export async function loginHandler(request: Request) {
  try {
    const payload = await credentials(request, false);
    const response = await authFetch("login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await checked(response);
    const data = await response.json();
    if (
      typeof data?.session_token !== "string" ||
      !TOKEN_PATTERN.test(data.session_token) ||
      typeof data.expires_at !== "string" ||
      !Number.isFinite(Date.parse(data.expires_at)) ||
      Date.parse(data.expires_at) <= Date.now()
    )
      throw new WebError("auth_unavailable", 503);
    const me = projectUser(data.me);
    const result = NextResponse.json(
      { ok: true, me },
      { headers: { "Cache-Control": "no-store" } },
    );
    setSession(result, data.session_token, data.expires_at);
    return result;
  } catch (error) {
    return authError(error);
  }
}
export async function logoutHandler(request: Request) {
  try {
    requireMutation(request);
  } catch (error) {
    return authError(error);
  }
  let confirmed = true;
  try {
    const cookie = await sessionCookie();
    if (cookie.token)
      confirmed =
        (
          await authFetch("logout", {
            method: "POST",
            headers: { Authorization: `Bearer ${cookie.token}` },
          })
        ).status === 204;
  } catch {
    confirmed = false;
  }
  const result = NextResponse.json(
    { ok: true, revocation_confirmed: confirmed },
    { headers: { "Cache-Control": "no-store" } },
  );
  clearSession(result);
  return result;
}
export async function meHandler() {
  const state = await getCurrentUser();
  if (state.kind === "unavailable")
    return authError(new WebError("auth_unavailable", 503));
  if (state.kind === "unauthenticated")
    return authError(new WebError("unauthenticated", 401));
  return NextResponse.json(state.user, {
    headers: { "Cache-Control": "no-store" },
  });
}

export function checkRateLimit(response: Response) {
  if (response.status !== 429) return;
  const retry = response.headers.get("Retry-After") ?? "";
  throw new WebError(
    "rate_limited",
    429,
    undefined,
    /^\d{1,6}$/.test(retry) && Number(retry) > 0 ? retry : undefined,
  );
}

export async function emailHandler(
  request: Request,
  operation:
    "email/resend" | "email/verify" | "password/forgot" | "password/reset",
) {
  try {
    requireMutation(request);
    const text = await request.text();
    if (text.length > 10000) throw new WebError("auth_invalid", 422);
    let input;
    try {
      input = JSON.parse(text);
    } catch {
      throw new WebError("auth_invalid", 422);
    }
    const requesting = ["email/resend", "password/forgot"].includes(operation);
    const fields = requesting
      ? ["email"]
      : operation === "password/reset"
        ? ["token", "password"]
        : ["token"];
    if (
      !input ||
      typeof input !== "object" ||
      Array.isArray(input) ||
      Object.keys(input).some((key) => !fields.includes(key)) ||
      fields.some((key) => typeof input[key] !== "string")
    )
      throw new WebError("auth_invalid", 422);
    const response = await authFetch(operation, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
    await checked(response);
    if (response.status !== (requesting ? 202 : 200))
      throw new WebError("auth_unavailable", 503);
    const result = NextResponse.json(
      { ok: true },
      {
        status: requesting ? 202 : 200,
        headers: { "Cache-Control": "no-store" },
      },
    );
    if (operation === "password/reset") clearSession(result);
    return result;
  } catch (error) {
    return authError(error);
  }
}
