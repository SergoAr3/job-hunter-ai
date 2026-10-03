export type CurrentUser = {
  email: string | null;
  email_verified: boolean;
  telegram_linked: boolean;
  display_name: string | null;
  profile_exists: boolean;
  created_at: string;
};

// Validate the destination path separately from query data. Never turn encoded
// query values into URL separators or decode a literal percent a second time.
export function safeNext(value: unknown): string {
  if (typeof value !== "string" || value.length > 2048) return "/profile";
  try {
    if (
      !value.startsWith("/") ||
      value.startsWith("//") ||
      /[\\\x00-\x1f\x7f]/.test(value)
    )
      return "/profile";
    const url = new URL(value, "https://internal.invalid");
    if (
      url.origin !== "https://internal.invalid" ||
      !/^\/(profile|discover|applications(?:\/[1-9]\d*)?)$/.test(url.pathname)
    )
      return "/profile";
    // Validate query encoding once, without using decoded text as a URL.
    // The original search retains encoded separators, pluses and percents.
    if (
      [url.search, url.hash].some((part) =>
        /[\\\x00-\x1f\x7f]/.test(decodeURIComponent(part)),
      )
    )
      return "/profile";
    return url.pathname + url.search;
  } catch {
    return "/profile";
  }
}
export function loginUrl(next: string) {
  return `/login?next=${encodeURIComponent(safeNext(next))}`;
}
