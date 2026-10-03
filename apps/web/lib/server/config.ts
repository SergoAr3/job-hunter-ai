import "server-only";
import { WebError } from "../errors";
export function getApiBase() {
  const base = process.env.API_BASE_URL;
  if (!base) throw new WebError("configuration", 503);
  let url: URL;
  try {
    url = new URL(base);
  } catch {
    throw new WebError("configuration", 503);
  }
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/"
  )
    throw new WebError("configuration", 503);
  return url.origin;
}
