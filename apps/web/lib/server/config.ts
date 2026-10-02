import "server-only";
import { WebError } from "../errors";
export function getConfig() {
  const id = process.env.WEB_DEV_USER_ID;
  const base = process.env.API_BASE_URL;
  const devToken = process.env.WEB_DEV_API_TOKEN;
  if (
    process.env.NODE_ENV === "production" ||
    process.env.APP_ENV !== "development" ||
    process.env.AUTH_ROLLOUT_MODE !== "legacy-development" ||
    !devToken ||
    !/^[\x21-\x7e]{32,512}$/.test(devToken)
  )
    throw new WebError("configuration", 503);
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
    url.pathname !== "/" ||
    !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
  )
    throw new WebError("configuration", 503);
  return { userId: id, baseUrl: url.origin, devToken };
}
