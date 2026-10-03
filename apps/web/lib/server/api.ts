import "server-only";
import { getDomainIdentity } from "./auth";
import { WebError } from "../errors";
import type {
  ApplicationDetail,
  ApplicationStatusHistory,
  ApplicationsPage,
  ApplicationsSummary,
  DiscoverPage,
  Identity,
  SaveResult,
} from "../contracts";
import {
  applicationsUrl,
  APPLICATIONS_PAGE_SIZE,
  type ApplicationsState,
  applicationStatuses,
  type ApplicationStatus,
} from "../applications";

async function request(
  path: string,
  body?: Identity | { status: ApplicationStatus },
  method: "POST" | "PUT" = "POST",
): Promise<unknown> {
  const { userId, baseUrl, headers } = await getDomainIdentity();
  const mutation = body ? method : "GET";
  const ambiguous = method === "PUT" ? "ambiguous_status" : "ambiguous_save";
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/users/${userId}/${path}`, {
      method: mutation,
      cache: "no-store",
      redirect: "error",
      headers: {
        ...headers,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(body ? 60000 : 35000),
    });
  } catch {
    throw new WebError(body ? ambiguous : "api_unavailable", 503);
  }
  if (response.status === 401) throw new WebError("unauthenticated", 401);
  let value;
  try {
    value = await response.json();
  } catch {
    throw new WebError(body ? ambiguous : "api_unavailable");
  }
  if (!response.ok) {
    const code = value?.detail?.code;
    const known = [
      "source_timeout",
      "source_unavailable",
      "source_rate_limited",
      "source_bad_response",
      "vacancy_not_found",
      "source_identity_conflict",
      "USER_NOT_FOUND",
      "APPLICATION_NOT_FOUND",
    ];
    throw new WebError(
      known.includes(code)
        ? code
        : body && response.status >= 500
          ? ambiguous
          : response.status === 422
            ? method === "PUT"
              ? "status_invalid"
              : "invalid_request"
            : body
              ? method === "PUT"
                ? "status_failed"
                : "save_failed"
              : "api_unavailable",
      response.status,
    );
  }
  return value;
}
export async function searchJobs(
  query: string,
  offset: number,
  remote: boolean,
): Promise<DiscoverPage> {
  const params = new URLSearchParams({
    market_country: "RU",
    query,
    offset: String(offset),
    limit: "10",
    remote_only: String(remote),
  });
  const data = (await request(`discover/jobs?${params}`)) as DiscoverPage;
  if (
    !Array.isArray(data?.items) ||
    data.offset !== offset ||
    !(
      data.next_offset === null ||
      (Number.isSafeInteger(data.next_offset) && data.next_offset > offset)
    )
  )
    throw new WebError("api_unavailable");
  return data;
}
// Explicit projection prevents the authenticated user's ID (or extra backend fields) reaching the browser.
export function publicDetail(data: ApplicationDetail): ApplicationDetail {
  if (
    !Number.isSafeInteger(data?.application?.id) ||
    data.application.id <= 0 ||
    typeof data.application.status !== "string" ||
    !data.job
  )
    throw new WebError("api_unavailable");
  const { id, status, note, next_action, next_action_due_on } =
    data.application;
  const {
    title,
    company,
    location,
    workplace_type,
    source_url,
    description,
    requirements_text,
    salary_text,
    salary_min,
    salary_max,
    salary_currency,
  } = data.job;
  return {
    application: { id, status, note, next_action, next_action_due_on },
    job: {
      title,
      company,
      location,
      workplace_type,
      source_url,
      description,
      requirements_text,
      salary_text,
      salary_min,
      salary_max,
      salary_currency,
    },
  };
}
export async function saveJob(identity: Identity): Promise<SaveResult> {
  const data = (await request("discover/jobs/save", identity)) as SaveResult;
  try {
    if (
      typeof data.application_created !== "boolean" ||
      typeof data.job_created !== "boolean"
    )
      throw new Error();
    return {
      ...publicDetail(data),
      application_created: data.application_created,
      job_created: data.job_created,
    };
  } catch {
    throw new WebError("ambiguous_save");
  }
}
export async function getApplication(id: string) {
  if (!/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id)))
    throw new WebError("APPLICATION_NOT_FOUND", 404);
  return publicDetail(
    (await request(`applications/${id}`)) as ApplicationDetail,
  );
}
export async function getApplicationStatusHistory(
  id: string,
): Promise<ApplicationStatusHistory> {
  if (!/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id)))
    throw new WebError("APPLICATION_NOT_FOUND", 404);
  const data = (await request(
    `applications/${id}/status-history`,
  )) as ApplicationStatusHistory;
  if (!Array.isArray(data?.items)) throw new WebError("api_unavailable");
  return {
    items: data.items.map((item) => {
      if (
        typeof item?.status !== "string" ||
        !applicationStatuses.includes(item.status as ApplicationStatus) ||
        typeof item.occurred_at !== "string" ||
        !/(?:Z|[+-]\d{2}:\d{2})$/i.test(item.occurred_at) ||
        Number.isNaN(Date.parse(item.occurred_at))
      )
        throw new WebError("api_unavailable");
      return { status: item.status, occurred_at: item.occurred_at };
    }),
  };
}
export async function setApplicationStatus(
  id: string,
  status: ApplicationStatus,
) {
  if (!/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id)))
    throw new WebError("APPLICATION_NOT_FOUND", 404);
  if (!applicationStatuses.includes(status))
    throw new WebError("status_invalid", 400);
  try {
    const detail = publicDetail(
      (await request(
        `applications/${id}/status`,
        { status },
        "PUT",
      )) as ApplicationDetail,
    );
    if (detail.application.id !== Number(id))
      throw new Error("Unexpected application");
    return { ok: true };
  } catch (error) {
    if (error instanceof WebError && error.code === "api_unavailable")
      throw new WebError("ambiguous_status", error.status);
    if (!(error instanceof WebError)) throw new WebError("ambiguous_status");
    throw error;
  }
}
export async function listApplications(
  state: ApplicationsState,
): Promise<ApplicationsPage> {
  const query = applicationsUrl(state).split("?")[1];
  const data = (await request(
    `applications?limit=${APPLICATIONS_PAGE_SIZE}${query ? `&${query}` : ""}`,
  )) as ApplicationsPage;
  if (
    !Array.isArray(data?.items) ||
    data.items.length > APPLICATIONS_PAGE_SIZE ||
    typeof data.has_next !== "boolean"
  )
    throw new WebError("api_unavailable");
  return {
    has_next: data.has_next,
    items: data.items.map((item) => {
      if (
        !Number.isSafeInteger(item?.app_id) ||
        item.app_id <= 0 ||
        typeof item.status !== "string" ||
        typeof item.created_at !== "string" ||
        ![item.title, item.company, item.location].every(
          (value) => value === null || typeof value === "string",
        ) ||
        typeof item.workplace_type !== "string" ||
        typeof item.parsing_status !== "string" ||
        typeof item.ai_enrichment_status !== "string"
      )
        throw new WebError("api_unavailable");
      const {
        app_id,
        status,
        created_at,
        title,
        company,
        location,
        workplace_type,
        parsing_status,
        ai_enrichment_status,
      } = item;
      return {
        app_id,
        status,
        created_at,
        title,
        company,
        location,
        workplace_type,
        parsing_status,
        ai_enrichment_status,
      };
    }),
  };
}
export async function errorResponse(error: unknown) {
  const { authError } = await import("./auth-handlers");
  return authError(
    error instanceof WebError ? error : new WebError("api_unavailable"),
  );
}

export async function getApplicationsSummary(): Promise<ApplicationsSummary> {
  const data = (await request("applications/summary")) as ApplicationsSummary;
  if (
    !Number.isSafeInteger(data?.total) ||
    data.total < 0 ||
    !data.status_counts ||
    Object.keys(data.status_counts).length !== applicationStatuses.length ||
    !applicationStatuses.every(
      (status) =>
        Number.isSafeInteger(data.status_counts[status]) &&
        data.status_counts[status] >= 0,
    ) ||
    applicationStatuses.reduce(
      (sum, status) => sum + data.status_counts[status],
      0,
    ) !== data.total
  )
    throw new WebError("api_unavailable");
  return {
    total: data.total,
    status_counts: Object.fromEntries(
      applicationStatuses.map((status) => [status, data.status_counts[status]]),
    ) as ApplicationsSummary["status_counts"],
  };
}
