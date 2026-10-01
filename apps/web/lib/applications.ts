export const APPLICATIONS_PAGE_SIZE = 5;
export const MAX_APPLICATIONS_OFFSET = 10_000;

export const applicationStatuses = [
  "saved",
  "applied",
  "recruiter_response",
  "interview",
  "offer",
  "hired",
  "withdrawn",
  "rejected",
] as const;
export const applicationSorts = ["newest", "oldest", "next_action"] as const;

export type ApplicationStatus = (typeof applicationStatuses)[number];
export type ApplicationSort = (typeof applicationSorts)[number];

export interface ApplicationsState {
  q: string;
  status: ApplicationStatus | null;
  sort: ApplicationSort;
  offset: number;
}

export function parseApplicationsState(
  params: URLSearchParams,
): ApplicationsState {
  const query = (params.get("q") ?? "").trim();
  const status = params.get("status");
  const sort = params.get("sort");
  const rawOffset = params.get("offset") ?? "0";
  const offset = Number(rawOffset);
  return {
    q: query.length <= 100 && !query.includes("\0") ? query : "",
    status: applicationStatuses.find((value) => value === status) ?? null,
    sort: applicationSorts.find((value) => value === sort) ?? "newest",
    offset:
      /^\d+$/.test(rawOffset) &&
      Number.isSafeInteger(offset) &&
      offset <= MAX_APPLICATIONS_OFFSET &&
      offset % APPLICATIONS_PAGE_SIZE === 0
        ? offset
        : 0,
  };
}

export function applicationsUrl(state: ApplicationsState): string {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.status) params.set("status", state.status);
  if (state.sort !== "newest") params.set("sort", state.sort);
  if (state.offset) params.set("offset", String(state.offset));
  return `/applications${params.size ? `?${params}` : ""}`;
}

export function safeApplicationsReturn(
  value: string | string[] | undefined,
): string {
  if (typeof value !== "string" || !value.startsWith("/applications"))
    return "/applications";
  try {
    const url = new URL(value, "http://local.invalid");
    if (
      url.origin !== "http://local.invalid" ||
      url.pathname !== "/applications" ||
      url.hash
    )
      return "/applications";
    const allowed = new Set(["q", "status", "sort", "offset"]);
    for (const key of url.searchParams.keys()) {
      if (!allowed.has(key) || url.searchParams.getAll(key).length !== 1)
        return "/applications";
    }
    return applicationsUrl(parseApplicationsState(url.searchParams));
  } catch {
    return "/applications";
  }
}
