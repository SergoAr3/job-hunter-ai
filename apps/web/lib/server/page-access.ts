import "server-only";
import { redirect } from "next/navigation";
import { loginUrl, safeNext } from "../auth";
import { WebError } from "../errors";
import { getDomainIdentity } from "./auth";
export async function pageAccess(path: string) {
  try {
    return await getDomainIdentity();
  } catch (error) {
    if (!(error instanceof WebError) || error.code !== "unauthenticated")
      return null;
  }
  redirect(loginUrl(safeNext(path)));
}
export async function pagePath(
  path: string,
  searchParams?: Promise<Record<string, string | string[] | undefined>>,
) {
  const params = await searchParams;
  const query = new URLSearchParams();
  if (params)
    for (const [key, value] of Object.entries(params)) {
      if (typeof value === "string") query.set(key, value);
    }
  return path + (query.size ? `?${query}` : "");
}
