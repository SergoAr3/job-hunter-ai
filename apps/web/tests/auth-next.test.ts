import { expect, it } from "vitest";
import { loginUrl, safeNext } from "../lib/auth";
it.each([
  "/dashboard",
  "/profile",
  "/profile/import",
  "/discover?q=python",
  "/applications",
  "/applications/123",
  "/discover?query=python%20developer",
])("permits internal workspace destination %s", (path) => {
  expect(safeNext(path)).toBe(path);
});
it.each([
  undefined,
  "https://evil.example",
  "http://evil.example",
  "//evil.example",
  "/\\evil.example",
  "%2f%2fevil.example",
  "/%255c%255cevil.example",
  "/login",
  "/register?next=/login",
  "/applications/0",
  "/api/auth/me",
  "/discover%00",
  "/discover%zz",
  "/\n/evil.example",
  "javascript:alert(1)",
  "/%2f%2fevil.example",
  "/%252f%252fevil.example",
  "/%5cevil.example",
  "/discover?q=%ZZ",
  "/discover?q=bad%",
  "/discover?q=%E0%A4%A",
  "/discover?q=%C0%AF",
  "/discover#%ZZ",
  "/discover#%0d%0a",
  "/discover?q=%0d%0aLocation:evil",
])("rejects unsafe next %s", (path) => {
  expect(safeNext(path)).toBe("/dashboard");
});

it.each([
  "/applications?q=R%26D",
  "/discover?q=C%2B%2B",
  "/applications?q=100%25",
  "/applications/86?from=%2Fapplications%3Fq%3DPython%26status%3Dsaved",
  "/applications?q=R%26D&status=saved&sort=oldest&offset=5",
  "/discover?query=python%20developer&remote=true&offset=10",
  "/discover?q=one&q=two+words&q=C%2B%2B",
])(
  "preserves query semantics across safe-next/login round trips: %s",
  (path) => {
    const expected = new URL(path, "https://internal.invalid");
    const next = safeNext(path);
    const actual = new URL(next, expected.origin);
    expect(actual.pathname).toBe(expected.pathname);
    expect([...actual.searchParams]).toEqual([...expected.searchParams]);
    expect(safeNext(next)).toBe(next);
    const login = new URL(loginUrl(next), expected.origin);
    expect(safeNext(login.searchParams.get("next"))).toBe(next);
  },
);
