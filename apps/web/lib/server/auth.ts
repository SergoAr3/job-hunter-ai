import "server-only";
import { cache } from "react";
import { cookies, headers } from "next/headers";
import { WebError } from "../errors";
import type { CurrentUser } from "../auth";
import { getApiBase, getConfig } from "./config";
import { cookieConfig, TOKEN_PATTERN } from "./cookie";

export function projectUser(value: unknown): CurrentUser {
  if (!value || typeof value !== "object")
    throw new WebError("auth_unavailable", 503);
  const v = value as Record<string, unknown>;
  if (
    ![v.email, v.display_name].every(
      (x) => x === null || typeof x === "string",
    ) ||
    ![v.email_verified, v.telegram_linked, v.profile_exists].every(
      (x) => typeof x === "boolean",
    ) ||
    typeof v.created_at !== "string" ||
    !Number.isFinite(Date.parse(v.created_at))
  )
    throw new WebError("auth_unavailable", 503);
  return {
    email: v.email as string | null,
    display_name: v.display_name as string | null,
    email_verified: v.email_verified as boolean,
    telegram_linked: v.telegram_linked as boolean,
    profile_exists: v.profile_exists as boolean,
    created_at: v.created_at,
  };
}
export async function authFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const base = getApiBase();
  try {
    return await fetch(`${base}/auth/${path}`, {
      ...init,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(15000),
    });
  } catch {
    throw new WebError("auth_unavailable", 503);
  }
}
export async function sessionCookie() {
  const name = cookieConfig().name;
  // Next drops undecodable values and collapses duplicate names. Determine
  // presence/ambiguity from the raw header before consulting its cookie store.
  const matches = ((await headers()).get("cookie") ?? "")
    .split(";")
    .filter((part) => part.split("=", 1)[0].trim() === name);
  if (!matches.length) return { present: false, token: null };
  if (matches.length !== 1 || !matches[0].includes("="))
    return { present: true, token: null };
  let decoded: string;
  try {
    decoded = decodeURIComponent(
      matches[0].slice(matches[0].indexOf("=") + 1).trim(),
    );
  } catch {
    return { present: true, token: null };
  }
  const all = (await cookies()).getAll(name);
  return {
    present: true,
    token:
      all.length === 1 &&
      all[0].value === decoded &&
      TOKEN_PATTERN.test(decoded)
        ? all[0].value
        : null,
  };
}
type AuthState =
  | { kind: "authenticated"; token: string; userId: string; user: CurrentUser }
  | { kind: "unauthenticated"; present: boolean }
  | { kind: "unavailable" };
export const getCurrentUser = cache(async (): Promise<AuthState> => {
  const cookie = await sessionCookie();
  if (!cookie.present || !cookie.token)
    return { kind: "unauthenticated", present: cookie.present };
  try {
    const response = await authFetch("internal/principal", {
      headers: { Authorization: `Bearer ${cookie.token}` },
    });
    if (response.status === 401)
      return { kind: "unauthenticated", present: true };
    if (!response.ok) return { kind: "unavailable" };
    const value = await response.json();
    if (!Number.isSafeInteger(value?.user_id) || value.user_id <= 0)
      return { kind: "unavailable" };
    return {
      kind: "authenticated",
      token: cookie.token,
      userId: String(value.user_id),
      user: projectUser(value.me),
    };
  } catch {
    return { kind: "unavailable" };
  }
});
type DomainIdentity = {
  userId: string;
  baseUrl: string;
  headers: Record<string, string>;
  user: CurrentUser | null;
  mode: "session" | "development";
};
export async function getDomainIdentity(): Promise<DomainIdentity> {
  const state = await getCurrentUser();
  if (state.kind === "authenticated")
    return {
      userId: state.userId,
      baseUrl: getApiBase(),
      headers: { Authorization: `Bearer ${state.token}` },
      user: state.user,
      mode: "session" as const,
    };
  if (state.kind === "unavailable") throw new WebError("auth_unavailable", 503);
  if (state.present) throw new WebError("unauthenticated", 401);
  if (
    process.env.APP_ENV === "development" &&
    process.env.AUTH_ROLLOUT_MODE === "legacy-development" &&
    process.env.NODE_ENV !== "production"
  ) {
    const config = getConfig();
    return {
      userId: config.userId,
      baseUrl: config.baseUrl,
      headers: { "X-Web-Dev-Api-Token": config.devToken },
      user: null,
      mode: "development" as const,
    };
  }
  throw new WebError("unauthenticated", 401);
}
