import "server-only";
import { getDomainIdentity } from "./auth";
import { requireMutation } from "./origin";
import { authError } from "./auth-handlers";
import { WebError } from "../errors";
import { CV_MAX_BYTES, cvFileError, type CVPreview } from "../cv-import";
import {
  experienceLevels,
  workplacePreferences,
  salaryPeriods,
  type ProfileDraft,
} from "../profile";

const MAX_BODY = CV_MAX_BYTES + 128 * 1024;
const codes = new Set([
  "file_too_large",
  "unsupported_file_type",
  "malformed_document",
  "no_extractable_text",
  "insufficient_job_information",
  "invalid_ai_output",
  "ai_provider_error",
  "ai_unavailable",
  "ai_timeout",
  "cv_file_empty",
  "cv_import_invalid",
  "cv_import_expired",
  "cv_import_used",
  "cv_import_stale",
  "cv_import_busy",
  "cv_import_limit",
  "cv_import_apply_failed",
  "cv_import_unconfirmed",
  "cv_import_unavailable",
  "cv_import_edit_invalid",
  "cv_import_revision_stale",
]);
const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);
const strings = (value: unknown, limit = 30): value is string[] =>
  Array.isArray(value) &&
  value.length <= limit &&
  value.every((item) => typeof item === "string" && item.length <= 500);

function profile(value: unknown): ProfileDraft {
  if (
    !record(value) ||
    !strings(value.target_roles) ||
    !strings(value.skills) ||
    !strings(value.location) ||
    !experienceLevels.includes(
      value.experience as ProfileDraft["experience"],
    ) ||
    !workplacePreferences.includes(
      value.workplace_preference as ProfileDraft["workplace_preference"],
    ) ||
    !salaryPeriods.includes(
      value.salary_period as ProfileDraft["salary_period"],
    ) ||
    !(value.salary_min === null || typeof value.salary_min === "string") ||
    !(
      value.salary_currency === null ||
      typeof value.salary_currency === "string"
    ) ||
    !Array.isArray(value.languages) ||
    value.languages.length > 30 ||
    !value.languages.every(
      (item) =>
        record(item) &&
        typeof item.language === "string" &&
        typeof item.level === "string",
    )
  )
    throw new WebError("cv_import_unavailable");
  return {
    target_roles: value.target_roles,
    skills: value.skills,
    location: value.location,
    experience: value.experience as ProfileDraft["experience"],
    workplace_preference:
      value.workplace_preference as ProfileDraft["workplace_preference"],
    salary_min: value.salary_min,
    salary_currency: value.salary_currency,
    salary_period: value.salary_period as ProfileDraft["salary_period"],
    languages: value.languages.map((item) => ({
      language: item.language,
      level: item.level,
    })),
  };
}
export function projectPreview(value: unknown): CVPreview {
  if (
    !record(value) ||
    typeof value.token !== "string" ||
    !Number.isSafeInteger(value.revision) ||
    (value.revision as number) < 1 ||
    !/^[A-Za-z0-9_-]{43}$/.test(value.token) ||
    typeof value.expires_at !== "number" ||
    !Number.isFinite(value.expires_at) ||
    !strings(value.experience_facts, 20) ||
    !Array.isArray(value.work_experience) ||
    value.work_experience.length > 20 ||
    value.work_experience_mode !== "replace" ||
    !Number.isSafeInteger(value.current_work_experience_count) ||
    (value.current_work_experience_count as number) < 0 ||
    value.experience_facts_mode !== "replace" ||
    !Number.isSafeInteger(value.current_experience_fact_count) ||
    (value.current_experience_fact_count as number) < 0
  )
    throw new WebError("cv_import_unavailable");
  const work = value.work_experience.map((item) => {
    if (
      !record(item) ||
      ![item.company, item.position].every(
        (text) => text === null || typeof text === "string",
      ) ||
      !["employment", "internship", "freelance", "unknown"].includes(
        String(item.engagement_kind),
      ) ||
      ![item.start_year, item.start_month, item.end_year, item.end_month].every(
        (date) => date === null || Number.isSafeInteger(date),
      ) ||
      !(item.is_current === null || typeof item.is_current === "boolean")
    )
      throw new WebError("cv_import_unavailable");
    return {
      company: item.company as string | null,
      position: item.position as string | null,
      engagement_kind:
        item.engagement_kind as CVPreview["work_experience"][number]["engagement_kind"],
      start_year: item.start_year as number | null,
      start_month: item.start_month as number | null,
      end_year: item.end_year as number | null,
      end_month: item.end_month as number | null,
      is_current: item.is_current as boolean | null,
    };
  });
  return {
    token: value.token,
    revision: value.revision as number,
    expires_at: value.expires_at,
    current: value.current === null ? null : profile(value.current),
    proposed: profile(value.proposed),
    work_experience: work,
    work_experience_mode: "replace",
    current_work_experience_count:
      value.current_work_experience_count as number,
    experience_facts_mode: "replace",
    current_experience_fact_count:
      value.current_experience_fact_count as number,
    experience_facts: value.experience_facts,
  };
}

async function boundedBody(
  request: Request,
  maximum: number,
): Promise<ArrayBuffer> {
  const declared = request.headers.get("content-length");
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > maximum))
    throw new WebError("file_too_large", 413);
  const reader = request.body?.getReader();
  if (!reader) throw new WebError("cv_file_empty", 422);
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maximum) {
        await reader.cancel();
        throw new WebError("file_too_large", 413);
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const result = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result.buffer;
}
async function upstream(
  identity: Awaited<ReturnType<typeof getDomainIdentity>>,
  action: string,
  body: FormData | string,
) {
  let response: Response;
  try {
    response = await fetch(
      `${identity.baseUrl}/users/${identity.userId}/profile/cv-import${action}`,
      {
        method: action === "/preview" ? "PATCH" : "POST",
        cache: "no-store",
        redirect: "error",
        headers: {
          ...identity.headers,
          ...(typeof body === "string"
            ? { "Content-Type": "application/json" }
            : {}),
        },
        body,
        signal: AbortSignal.timeout(action ? 20000 : 80000),
      },
    );
  } catch {
    throw new WebError(
      action === "/apply" ? "cv_import_unconfirmed" : "cv_import_unavailable",
      503,
    );
  }
  if (response.status === 401) throw new WebError("unauthenticated", 401);
  let value;
  try {
    value = await response.json();
  } catch {
    throw new WebError(
      action === "/apply" ? "cv_import_unconfirmed" : "cv_import_unavailable",
    );
  }
  if (!response.ok) {
    const code =
      typeof value?.detail === "string" ? value.detail : value?.detail?.code;
    throw new WebError(
      codes.has(code) ? code : "cv_import_unavailable",
      response.status,
      code === "cv_import_edit_invalid" && record(value?.detail?.fieldErrors)
        ? (Object.fromEntries(
            Object.entries(value.detail.fieldErrors).filter(
              ([key, text]) =>
                [
                  "company",
                  "position",
                  "engagement_kind",
                  "start_year",
                  "start_month",
                  "end_year",
                  "end_month",
                  "is_current",
                  "dates",
                  "text",
                  "target_roles",
                  "skills",
                  "location",
                  "experience",
                  "workplace_preference",
                  "salary_min",
                  "salary_currency",
                  "salary_period",
                  "languages",
                  "edit",
                ].includes(key) &&
                typeof text === "string" &&
                text.length <= 160,
            ),
          ) as Record<string, string>)
        : undefined,
    );
  }
  return value;
}
export async function uploadCV(request: Request) {
  try {
    if (new URL(request.url).search) throw new WebError("invalid_request", 400);
    requireMutation(request, "multipart");
    const identity = await getDomainIdentity();
    const body = await boundedBody(request, MAX_BODY);
    let form: FormData;
    try {
      form = await new Request(request.url, {
        method: "POST",
        headers: request.headers,
        body,
      }).formData();
    } catch {
      throw new WebError("invalid_request", 400);
    }
    const file = form.get("file");
    if ([...form.keys()].length !== 1 || !(file instanceof File))
      throw new WebError("invalid_request", 400);
    const error = cvFileError(file);
    if (error)
      throw new WebError(error, error === "file_too_large" ? 413 : 422);
    const forward = new FormData();
    forward.set("file", file);
    return Response.json(
      projectPreview(await upstream(identity, "", forward)),
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return authError(
      error instanceof WebError
        ? error
        : new WebError("cv_import_unavailable", 503),
    );
  }
}
export async function importAction(
  request: Request,
  action: "/apply" | "/cancel",
) {
  try {
    if (new URL(request.url).search) throw new WebError("invalid_request", 400);
    requireMutation(request);
    const identity = await getDomainIdentity();
    let value;
    try {
      value = JSON.parse(
        new TextDecoder().decode(await boundedBody(request, 1024)),
      );
    } catch (error) {
      if (error instanceof WebError) throw error;
      throw new WebError("invalid_request", 400);
    }
    if (
      !record(value) ||
      Object.keys(value).some((key) => !["token", "revision"].includes(key)) ||
      (value.revision !== undefined &&
        (!Number.isSafeInteger(value.revision) ||
          (value.revision as number) < 1)) ||
      typeof value.token !== "string" ||
      !/^[A-Za-z0-9_-]{43}$/.test(value.token)
    )
      throw new WebError("invalid_request", 400);
    const result = await upstream(
      identity,
      action,
      JSON.stringify({
        token: value.token,
        ...(value.revision !== undefined ? { revision: value.revision } : {}),
      }),
    );
    if (result?.ok !== true)
      throw new WebError(
        action === "/apply" ? "cv_import_unconfirmed" : "cv_import_unavailable",
      );
    return Response.json(
      { ok: true },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return authError(
      error instanceof WebError
        ? error
        : new WebError("cv_import_unavailable", 503),
    );
  }
}

export async function editCV(request: Request) {
  try {
    if (new URL(request.url).search) throw new WebError("invalid_request", 400);
    requireMutation(request);
    const identity = await getDomainIdentity();
    let value;
    try {
      value = JSON.parse(
        new TextDecoder().decode(await boundedBody(request, 64 * 1024)),
      );
    } catch {
      throw new WebError("cv_import_edit_invalid", 422);
    }
    if (
      !record(value) ||
      Object.keys(value).some(
        (key) => !["token", "revision", "edit"].includes(key),
      ) ||
      typeof value.token !== "string" ||
      !/^[A-Za-z0-9_-]{43}$/.test(value.token) ||
      !Number.isSafeInteger(value.revision) ||
      (value.revision as number) < 1 ||
      !record(value.edit) ||
      !["profile", "work", "delete_work", "fact", "delete_fact"].includes(
        String(value.edit.kind),
      )
    )
      throw new WebError("cv_import_edit_invalid", 422);
    return Response.json(
      projectPreview(
        await upstream(identity, "/preview", JSON.stringify(value)),
      ),
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return authError(
      error instanceof WebError
        ? error
        : new WebError("cv_import_unavailable", 503),
    );
  }
}
