// Display only: never use this value for API requests or source identity.
export function compactLocation(location: string | null): string {
  if (!location) return "";
  const seen = new Set<string>();
  return location
    .split(",")
    .map((segment) => segment.trim())
    .filter((segment) => {
      if (!segment) return false;
      const key = segment.toLocaleLowerCase("ru").replace(/\s+/g, " ");
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 2)
    .join(", ");
}
