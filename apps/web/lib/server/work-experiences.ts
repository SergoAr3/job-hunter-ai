import "server-only";
import { WebError } from "../errors";
import type { WorkExperienceEntry } from "../profile";
import {
  engagementKinds,
  workFields,
  workFieldLabels,
  type WorkInput,
} from "../work-experiences";
import { getDomainIdentity } from "./auth";
import { requireMutation } from "./origin";

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
export function readWorkExperience(value: unknown): WorkExperienceEntry {
  if (
    !record(value) ||
    !Number.isSafeInteger(value.id) ||
    (value.id as number) <= 0 ||
    ![value.company, value.position].every(
      (v) => v === null || typeof v === "string",
    ) ||
    !engagementKinds.includes(
      value.engagement_kind as WorkInput["engagement_kind"],
    ) ||
    ![
      value.start_year,
      value.start_month,
      value.end_year,
      value.end_month,
      value.duration_months,
    ].every((v) => v === null || Number.isSafeInteger(v)) ||
    !(value.is_current === null || typeof value.is_current === "boolean")
  )
    throw new WebError("api_unavailable");
  return Object.fromEntries(
    ["id", ...workFields, "duration_months"].map((key) => [key, value[key]]),
  ) as unknown as WorkExperienceEntry;
}

// Bound the stream, including requests without Content-Length. Only transport
// shape is checked here; normalization and domain invariants belong to the API.
export async function workMutationBody(
  request: Request,
): Promise<Partial<WorkInput>> {
  if (new URL(request.url).search) throw new WebError("invalid_request", 400);
  requireMutation(request);
  const declared = request.headers.get("content-length");
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > 8192))
    throw new WebError("invalid_request", 413);
  const reader = request.body?.getReader();
  if (!reader) throw new WebError("invalid_request", 400);
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > 8192) {
        await reader.cancel();
        throw new WebError("invalid_request", 413);
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  let value: unknown;
  try {
    value = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new WebError("invalid_request", 400);
  }
  if (
    !record(value) ||
    Object.entries(value).some(
      ([key, v]) =>
        !workFields.includes(key as keyof WorkInput) ||
        (key === "company" || key === "position"
          ? !(v === null || typeof v === "string")
          : key === "engagement_kind"
            ? !engagementKinds.includes(v as WorkInput["engagement_kind"])
            : key === "is_current"
              ? !(v === null || typeof v === "boolean")
              : !(v === null || Number.isSafeInteger(v))),
    )
  )
    throw new WebError("work_invalid", 422);
  return value as Partial<WorkInput>;
}

export async function mutateWork(
  method: "POST" | "PATCH" | "DELETE",
  payload: Partial<WorkInput>,
  id?: string,
) {
  if (
    id !== undefined &&
    (!/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id)))
  )
    throw new WebError("work_not_found", 404);
  const { userId, baseUrl, headers } = await getDomainIdentity();
  let response: Response;
  try {
    response = await fetch(
      `${baseUrl}/users/${userId}/profile/work-experiences${id ? `/${id}` : ""}`,
      {
        method,
        cache: "no-store",
        redirect: "error",
        headers: { ...headers, "Content-Type": "application/json" },
        body: method === "DELETE" ? undefined : JSON.stringify(payload),
        signal: AbortSignal.timeout(60000),
      },
    );
  } catch {
    throw new WebError("work_unconfirmed", 503);
  }
  if (response.status === 401) throw new WebError("unauthenticated", 401);
  if (method === "DELETE" && response.status === 204) return { ok: true };
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    throw new WebError("work_unconfirmed", 502);
  }
  if (!response.ok) {
    if (response.status === 404) throw new WebError("work_not_found", 404);
    if (response.status === 403) throw new WebError("work_forbidden", 403);
    if (response.status === 409) throw new WebError("work_conflict", 409);
    if (response.status === 422) {
      const detail = record(value) ? value.detail : null;
      const code = record(detail) ? detail.code : null;
      if (code === "DUPLICATE_WORK_EXPERIENCE")
        throw new WebError("work_duplicate", 422);
      if (code === "WORK_EXPERIENCE_LIMIT_REACHED")
        throw new WebError("work_limit", 422);
      const fields: Record<string, string> = {};
      if (Array.isArray(detail))
        for (const item of detail) {
          const key = item?.loc?.[1] as keyof WorkInput;
          if (workFields.includes(key))
            fields[key] = `Проверьте поле «${workFieldLabels[key]}».`;
        }
      throw new WebError("work_invalid", 422, fields);
    }
    throw new WebError("work_unconfirmed", 503);
  }
  try {
    const entry = readWorkExperience(value);
    if (id && entry.id !== Number(id)) throw new Error();
    return entry;
  } catch {
    throw new WebError("work_unconfirmed", 502);
  }
}
