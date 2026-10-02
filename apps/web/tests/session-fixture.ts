// Domain projection tests use a resolved real-session principal. Resolver and
// per-request authorization are exercised independently in auth-server tests.
import { getApiBase } from "../lib/server/config";
export function sessionIdentity() {
  return Promise.resolve({
    userId: "987",
    baseUrl: getApiBase(),
    headers: { Authorization: `Bearer ${"T".repeat(43)}` },
  });
}
