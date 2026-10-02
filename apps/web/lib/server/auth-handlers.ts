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
async function checked(response: Response) {
  if (response.ok) return;
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
