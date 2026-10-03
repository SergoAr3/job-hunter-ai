import "server-only";
import { NextResponse } from "next/server";
export const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const ABSOLUTE_SECONDS = 7 * 24 * 60 * 60;
export function cookieConfig() {
  const secure =
    process.env.NODE_ENV === "production" ||
    process.env.APP_ENV === "production";
  return {
    name: secure ? "__Host-job_hunter_session" : "job_hunter_session_dev",
    httpOnly: true,
    secure,
    sameSite: "lax" as const,
    path: "/",
  };
}
export function setSession(
  response: NextResponse,
  token: string,
  expiry: string,
) {
  const expires = new Date(
    Math.min(Date.parse(expiry), Date.now() + ABSOLUTE_SECONDS * 1000),
  );
  const maxAge = Math.max(
    0,
    Math.floor((expires.getTime() - Date.now()) / 1000),
  );
  response.cookies.set({ ...cookieConfig(), value: token, expires, maxAge });
}
export function clearSession(response: NextResponse) {
  response.cookies.set({
    ...cookieConfig(),
    value: "",
    expires: new Date(0),
    maxAge: 0,
  });
}
