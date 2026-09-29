import "server-only";
import { getConfig } from "./config";
import { WebError } from "../errors";
import type {
  ApplicationDetail,
  DiscoverPage,
  Identity,
  SaveResult,
} from "../contracts";

async function request(path: string, body?: Identity): Promise<unknown> {
  const { userId, baseUrl } = getConfig();
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/users/${userId}/${path}`, {
      method: body ? "POST" : "GET",
      cache: "no-store",
      redirect: "error",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(body ? 60000 : 35000),
    });
  } catch {
    throw new WebError(body ? "ambiguous_save" : "api_unavailable", 503);
  }
  let value;
  try {
    value = await response.json();
  } catch {
    throw new WebError(body ? "ambiguous_save" : "api_unavailable");
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
          ? "ambiguous_save"
          : response.status === 422
            ? "invalid_request"
            : body
              ? "save_failed"
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
// Explicit projection prevents the configured user's ID (or extra backend fields) reaching the browser.
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
export function errorResponse(error: unknown) {
  const safe =
    error instanceof WebError ? error : new WebError("api_unavailable");
  return Response.json(
    { code: safe.code },
    { status: safe.status, headers: { "Cache-Control": "no-store" } },
  );
}
