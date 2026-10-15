export interface DiscoverState {
  query: string;
  remote: boolean;
  offset: number;
  selected: string | null;
}

export function vacancyKey(item: {
  source: string;
  source_scope: string;
  external_id: string;
}): string {
  return JSON.stringify([item.source, item.source_scope, item.external_id]);
}

function selectedIdentity(value: string | null): string | null {
  if (!value || value.length > 800) return null;
  try {
    const parts: unknown = JSON.parse(value);
    return Array.isArray(parts) &&
      parts.length === 3 &&
      parts.every(
        (part) =>
          typeof part === "string" &&
          part.length > 0 &&
          part.length <= 256 &&
          !/[\x00-\x1f\x7f]/.test(part),
      )
      ? JSON.stringify(parts)
      : null;
  } catch {
    return null;
  }
}

export function parseDiscoverState(params: URLSearchParams): DiscoverState {
  const single = (key: string) =>
    params.getAll(key).length === 1 ? params.get(key) : null;
  const query = (single("query") ?? "").trim();
  if (!query || query.length > 200 || /[\x00-\x1f\x7f]/.test(query))
    return { query: "", remote: false, offset: 0, selected: null };
  const rawOffset = single("offset") ?? "0";
  const offset = Number(rawOffset);
  return {
    query,
    remote: single("remote_only") === "true",
    // Offset is the source's record offset, not a page number or an index.
    offset:
      /^\d+$/.test(rawOffset) && Number.isSafeInteger(offset) ? offset : 0,
    selected: selectedIdentity(single("selected")),
  };
}

export function discoverUrl(state: DiscoverState): string {
  const params = new URLSearchParams();
  if (state.query) {
    params.set("query", state.query);
    if (state.remote) params.set("remote_only", "true");
    if (state.offset) params.set("offset", String(state.offset));
    if (state.selected) params.set("selected", state.selected);
  }
  return `/discover${params.size ? `?${params}` : ""}`;
}
