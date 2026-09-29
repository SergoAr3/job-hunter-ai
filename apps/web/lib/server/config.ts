import "server-only";
import { WebError } from "../errors";
export function getConfig() {
  const id = process.env.WEB_DEV_USER_ID;
  const base = process.env.API_BASE_URL;
  if (
    !id ||
    !/^[1-9]\d*$/.test(id) ||
    !Number.isSafeInteger(Number(id)) ||
    !base
  )
    throw new WebError("configuration", 503);
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
  return { userId: id, baseUrl: url.origin };
}
