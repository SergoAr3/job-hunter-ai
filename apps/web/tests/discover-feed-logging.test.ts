// @vitest-environment node
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { expect, it } from "vitest";
import nextConfig from "../next.config";
import { discoverUrl } from "../lib/discover";
import { loginUrl } from "../lib/auth";

const token = "TOKEN_ABC" + "Z".repeat(23);
const discover = discoverUrl({
  query: "Python",
  remote: true,
  offset: 0,
  selected: null,
  mode: "feed",
  cursor: token,
});
const application = `/applications/42?from=${encodeURIComponent(discover)}`;
const legacy = "/discover?query=Python&remote_only=true&offset=10";
const require = createRequire(import.meta.url);
const loggerPath = require.resolve("next/dist/server/dev/log-requests.js");
const logging = nextConfig.logging;
if (!logging || typeof logging.incomingRequests !== "object")
  throw new Error("Expected selective incoming request logging config");
const patterns = logging.incomingRequests.ignore!.map(({ source, flags }) => ({
  source,
  flags,
}));

// Capture the real framework logger in a child process; do not mock console or
// substitute a sanitizer for the integration that originally leaked the URL.
function log(url: string, method = "GET", status = 200) {
  const result = spawnSync(
    process.execPath,
    [
      "-e",
      `
    const { logRequests } = require(process.argv[1]);
    const input = JSON.parse(process.argv[2]);
    const config = { incomingRequests: { ignore: input.patterns.map(p => new RegExp(p.source, p.flags)) } };
    const request = { method: input.method, url: input.url };
    logRequests(request, { statusCode: input.status }, config, 1n, 1000001n);
    if (request.url !== input.url) throw new Error('Logging mutated the request URL');
  `,
      loggerPath,
      JSON.stringify({ url, method, status, patterns }),
    ],
    { encoding: "utf8", timeout: 5000 },
  );
  expect(result.error).toBeUndefined();
  expect(result.status).toBe(0);
  expect(result.stderr).toBe("");
  return result.stdout;
}

it.each([
  discover,
  `/api/discover/feed?cursor=${token}`,
  `/api/discover/feed?other=1&cursor=${token}`,
  application,
  application + "&_rsc=test",
  loginUrl(discover),
  loginUrl(application),
  `/register?next=${encodeURIComponent(discover)}`,
  `/register?next=${encodeURIComponent(application)}`,
  `/login?registered=1&next=${encodeURIComponent(application)}`,
])(
  "suppresses a cursor-bearing line through the real Next logger: %s",
  (url) => {
    const output = log(url);
    expect(output).not.toContain(token);
    expect(output).toBe("");
  },
);

it.each([
  "/dashboard",
  "/discover",
  "/discover?query=Python",
  legacy,
  "/api/discover?query=Python&remote_only=true&offset=10",
  "/applications/42",
  `/applications/42?from=${encodeURIComponent(legacy)}`,
  loginUrl(legacy),
  loginUrl(`/applications/42?from=${encodeURIComponent(legacy)}`),
  "/register?next=%2Fdashboard",
  "/other?cursor=unrelated-value",
])("preserves ordinary method/path/status output: %s", (url) => {
  const output = log(url, "GET", 204);
  expect(output).toContain(`GET ${url} 204 in`);
});

it("preserves cursorless POST start/advance request lines", () => {
  expect(log("/api/discover/feed", "POST")).toContain(
    "POST /api/discover/feed 200 in",
  );
});
