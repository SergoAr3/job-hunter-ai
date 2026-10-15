import { expect, it } from "vitest";
import { discoverUrl, parseDiscoverState, vacancyKey } from "../lib/discover";
import { safeApplicationReturn } from "../lib/application-return";

const identity = vacancyKey({
  source: "trudvsem",
  source_scope: "company-1",
  external_id: "job-1",
});
it("round-trips confirmed query, remote, source offset and stable identity", () => {
  const state = {
    query: "C++ & Python",
    remote: true,
    offset: 13,
    selected: identity,
  };
  const url = discoverUrl(state);
  expect(parseDiscoverState(new URLSearchParams(url.split("?")[1]))).toEqual(
    state,
  );
  expect(safeApplicationReturn(url)).toBe(url);
});
it.each([
  "",
  "query=",
  "query=%20",
  "query=x&query=y",
  "query=%00Python",
  `query=${"x".repeat(201)}`,
])("normalizes missing/invalid query without a search: %s", (raw) => {
  expect(discoverUrl(parseDiscoverState(new URLSearchParams(raw)))).toBe(
    "/discover",
  );
});
it.each(["-10", "NaN", "Infinity", "1.5", "9007199254740992", "10&offset=20"])(
  "normalizes unsafe offset %s",
  (offset) => {
    expect(
      parseDiscoverState(new URLSearchParams(`query=Python&offset=${offset}`))
        .offset,
    ).toBe(0);
  },
);
it("ignores unknown, invalid remote and invalid selection without discarding valid query", () => {
  const state = parseDiscoverState(
    new URLSearchParams(
      "query=%20Python%20&remote_only=yes&extra=1&selected=0",
    ),
  );
  expect(discoverUrl(state)).toBe("/discover?query=Python");
});
it.each(["1", "[1,2,3]", '["a","b"]', '["a","b",""]', '["a","b","c","d"]'])(
  "rejects non-identity selection %s",
  (selected) => {
    const params = new URLSearchParams({ query: "Python", selected });
    expect(parseDiscoverState(params).selected).toBeNull();
  },
);
it.each([
  undefined,
  ["/discover?query=x"],
  "https://evil.test/discover?query=x",
  "//evil.test/discover?query=x",
  "/\\evil.test/discover?query=x",
  "/discover/other?query=x",
  "/discover?query=x#frag",
  "/discover?query=x&next=https://evil.test",
  "/discover?query=x&query=y",
  "/discover?offset=10",
  "/applications/42",
  "/applications?next=https://evil.test",
])("does not accept unsafe/unsupported return context %s", (value) => {
  expect(safeApplicationReturn(value)).toBe("/applications");
});
it("preserves the existing Applications list return contract", () => {
  const url = "/applications?q=Python&status=offer&sort=oldest&offset=10";
  expect(safeApplicationReturn(url)).toBe(url);
  expect(safeApplicationReturn("/applications")).toBe("/applications");
});
