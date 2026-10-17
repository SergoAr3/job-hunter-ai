import type { DiscoverFeedPage, Preview, Reason, Vacancy } from "./contracts";
import { applicationStatuses } from "./applications";
import { validFeedCursor, vacancyKey } from "./discover";
import { WebError } from "./errors";

function invalid(): never {
  throw new WebError("feed_unavailable", 502);
}
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : invalid();
}
function text(value: unknown, max = 262144): string {
  return typeof value === "string" && value.length <= max ? value : invalid();
}
function nullableText(value: unknown): string | null {
  return value === null ? null : text(value);
}
function number(value: unknown): number | null {
  return value === null
    ? null
    : typeof value === "number" && Number.isFinite(value)
      ? value
      : invalid();
}
function reason(value: unknown): Reason {
  const data = object(value);
  return {
    code: text(data.code, 200),
    component: text(data.component, 200),
    ...(data.value !== undefined ? { value: nullableText(data.value) } : {}),
  };
}
function preview(value: unknown): Preview {
  const data = object(value);
  if (
    typeof data.available !== "boolean" ||
    ![null, "high", "medium", "low", "insufficient_data"].includes(
      data.verdict as string | null,
    ) ||
    ![null, "high", "medium", "low"].includes(data.confidence as string | null)
  )
    invalid();
  const reasons = (value: unknown) =>
    Array.isArray(value) ? value.map(reason) : invalid();
  const recommendation =
    data.recommendation === null ? null : object(data.recommendation);
  return {
    available: data.available,
    unavailable_reason: nullableText(data.unavailable_reason ?? null),
    score: number(data.score),
    verdict: data.verdict as Preview["verdict"],
    confidence: data.confidence as Preview["confidence"],
    coverage: number(data.coverage),
    strengths: reasons(data.strengths),
    gaps: reasons(data.gaps),
    unknowns: reasons(data.unknowns),
    conflicts: reasons(data.conflicts),
    recommendation: recommendation
      ? {
          code: text(recommendation.code, 200),
          ...(recommendation.primary_reason != null
            ? { primary_reason: reason(recommendation.primary_reason) }
            : {}),
        }
      : null,
  };
}
function vacancy(value: unknown): Vacancy {
  const data = object(value);
  if (
    typeof data.already_saved_for_user !== "boolean" ||
    !(
      data.application_id === null ||
      (Number.isSafeInteger(data.application_id) &&
        (data.application_id as number) > 0)
    ) ||
    !(
      data.application_status === null ||
      applicationStatuses.some((status) => status === data.application_status)
    )
  )
    invalid();
  const identity = {
    source: text(data.source, 32),
    source_scope: text(data.source_scope, 255),
    external_id: text(data.external_id, 255),
  };
  if (
    Object.values(identity).some(
      (value) => !value || /[\x00-\x1f\x7f]/.test(value),
    ) ||
    data.workplace_type !== "remote"
  )
    invalid();
  return {
    ...identity,
    source_url: text(data.source_url),
    title: text(data.title),
    company: text(data.company),
    location: nullableText(data.location),
    description: nullableText(data.description),
    requirements_text: nullableText(data.requirements_text),
    workplace_type: "remote",
    salary_text: nullableText(data.salary_text),
    salary_min: number(data.salary_min),
    salary_max: number(data.salary_max),
    salary_currency: nullableText(data.salary_currency),
    preview_match: preview(data.preview_match),
    already_saved_for_user: data.already_saved_for_user,
    application_id: data.application_id as number | null,
    application_status:
      data.application_status as Vacancy["application_status"],
  };
}
export function projectDiscoverFeed(value: unknown): DiscoverFeedPage {
  const data = object(value);
  if (
    !validFeedCursor(data.cursor) ||
    !["page", "paused", "end"].includes(data.state as string) ||
    !["next", "continue", null].includes(data.next_action as string | null) ||
    ![data.prev_cursor, data.next_cursor].every(
      (value) => value === null || validFeedCursor(value),
    ) ||
    (data.next_action === null) !== (data.next_cursor === null) ||
    data.prev_cursor === data.cursor ||
    data.next_cursor === data.cursor ||
    typeof data.expires_at !== "string" ||
    !/(?:Z|[+-]\d{2}:\d{2})$/.test(data.expires_at) ||
    !Number.isFinite(Date.parse(data.expires_at)) ||
    !Array.isArray(data.items) ||
    data.items.length > 10 ||
    data.returned_count !== data.items.length ||
    (data.state === "page") !== data.items.length > 0 ||
    (data.state === "end" && data.next_cursor !== null)
  )
    invalid();
  const items = data.items.map(vacancy);
  if (new Set(items.map(vacancyKey)).size !== items.length) invalid();
  return {
    items,
    returned_count: items.length,
    cursor: data.cursor,
    prev_cursor: data.prev_cursor as string | null,
    next_cursor: data.next_cursor as string | null,
    next_action: data.next_action as DiscoverFeedPage["next_action"],
    state: data.state as DiscoverFeedPage["state"],
    expires_at: data.expires_at,
  };
}
