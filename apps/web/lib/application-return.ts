import { safeApplicationsReturn } from "./applications";
import { discoverUrl, parseDiscoverState } from "./discover";

// Extend the existing `from` contract; never navigate to an arbitrary URL.
export function safeApplicationReturn(
  value: string | string[] | undefined,
): string {
  if (
    typeof value !== "string" ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    /[\\\x00-\x1f\x7f]/.test(value)
  )
    return "/applications";
  try {
    const url = new URL(value, "http://local.invalid");
    if (url.origin !== "http://local.invalid" || url.hash)
      return "/applications";
    if (url.pathname !== "/discover") return safeApplicationsReturn(value);
    const allowed = new Set(["query", "remote_only", "offset", "selected"]);
    for (const key of url.searchParams.keys())
      if (!allowed.has(key) || url.searchParams.getAll(key).length !== 1)
        return "/applications";
    const state = parseDiscoverState(url.searchParams);
    return state.query ? discoverUrl(state) : "/applications";
  } catch {
    return "/applications";
  }
}
