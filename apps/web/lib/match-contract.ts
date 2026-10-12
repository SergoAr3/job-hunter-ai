import { WebError } from "./errors";

export const matchComponents = [
  "role",
  "required_skills",
  "nice_to_have_skills",
  "seniority",
  "languages",
  "workplace",
  "location",
  "salary",
] as const;
export type MatchComponentName = (typeof matchComponents)[number];
export type MatchStatus = "matched" | "partial" | "mismatch" | "unknown";
export type MatchVerdict = "high" | "medium" | "low" | "insufficient_data";
export type MatchConfidence = "high" | "medium" | "low";
export const matchReasonCodes = [
  "role_matched",
  "role_partial",
  "role_not_matched",
  "vacancy_role_unknown",
  "profile_target_roles_missing",
  "required_skill_listed",
  "required_skill_not_listed",
  "nice_to_have_skill_listed",
  "nice_to_have_skill_not_listed",
  "vacancy_skills_unavailable",
  "profile_skills_missing",
  "seniority_matches",
  "seniority_below_requirement",
  "vacancy_seniority_unknown",
  "profile_seniority_unknown",
  "language_level_sufficient",
  "language_not_listed",
  "language_level_below_requirement",
  "language_requirements_unparseable",
  "vacancy_language_requirements_unavailable",
  "workplace_matches",
  "workplace_not_preferred",
  "vacancy_workplace_unknown",
  "location_matches",
  "location_not_listed",
  "vacancy_location_missing",
  "profile_locations_missing",
  "salary_meets_expectations",
  "salary_below_minimum",
  "vacancy_salary_missing",
  "profile_salary_missing",
  "salary_period_inferred",
  "salary_not_comparable",
] as const;
export type MatchReason = {
  code: (typeof matchReasonCodes)[number];
  component: MatchComponentName;
  value: string | null;
};
export const explanationCodes = [
  "job_skills_missing",
  "job_skills_unavailable",
  "profile_skills_missing",
  "job_languages_missing",
  "job_languages_unavailable",
  "language_requirement_unparseable",
  "profile_language_level_unknown",
  "job_role_missing",
  "profile_roles_missing",
  "job_seniority_unknown",
  "profile_seniority_unknown",
  "job_workplace_unknown",
  "workplace_any",
  "remote_location_not_compared",
  "location_not_compared",
  "job_location_missing",
  "profile_locations_missing",
  "job_salary_missing",
  "profile_salary_missing",
  "job_salary_currency_missing",
  "profile_salary_currency_missing",
  "salary_currencies_incompatible",
  "job_salary_period_unknown",
  "profile_salary_period_unknown",
  "salary_period_inferred",
  "salary_not_comparable",
] as const;
export type MatchExplanationItem = {
  code: (typeof explanationCodes)[number];
  source: "profile" | "job" | "comparison";
  value: string | null;
};
export interface MatchComponent {
  weight: number;
  score: number | null;
  status: MatchStatus;
  matched: string[];
  missing: string[];
}
export interface MatchComponentExplanation {
  notices: MatchExplanationItem[];
  unknown_items: MatchExplanationItem[];
}
export interface MatchExplainability {
  schema_version: 1;
  components: Record<MatchComponentName, MatchComponentExplanation>;
}
export interface MatchInputState {
  profile_updated_at: string | null;
  job_updated_at: string | null;
  parsing_status:
    "not_attempted" | "pending" | "success" | "partial" | "failed";
  ai_enrichment_status: "not_attempted" | "pending" | "success" | "failed";
}
export interface MatchRecommendation {
  code: "apply" | "apply_with_risks" | "unlikely_fit" | "insufficient_data";
  primary_reason: MatchReason | null;
}
export interface MatchResult {
  algorithm_version: "job-match-v2.1" | "job-match-v2.2";
  application_id: number;
  job_id: number;
  score: number | null;
  verdict: MatchVerdict;
  coverage: number;
  confidence: MatchConfidence | null;
  input_state: MatchInputState;
  components: Record<MatchComponentName, MatchComponent>;
  strengths: MatchReason[];
  gaps: MatchReason[];
  unknowns: MatchReason[];
  conflicts: MatchReason[];
  recommendation: MatchRecommendation;
  explainability?: MatchExplainability | null;
}

function invalid(): never {
  throw new WebError("api_unavailable", 502);
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return invalid();
  return value as Record<string, unknown>;
}
function member<const T extends readonly string[]>(
  value: unknown,
  allowed: T,
): T[number] {
  if (typeof value !== "string" || !allowed.includes(value)) return invalid();
  return value as T[number];
}
function number(value: unknown, minimum: number, maximum: number): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < minimum ||
    value > maximum
  )
    return invalid();
  return value;
}
function text(value: unknown): string {
  if (typeof value !== "string" || value.length > 2000) return invalid();
  return value;
}
function nullableText(value: unknown): string | null {
  return value == null ? null : text(value);
}
function timestamp(value: unknown): string | null {
  if (value === null) return null;
  const result = text(value);
  if (
    !/(?:Z|[+-]\d{2}:\d{2})$/.test(result) ||
    !Number.isFinite(Date.parse(result))
  )
    return invalid();
  return result;
}
function list<T>(value: unknown, project: (item: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length > 1000) return invalid();
  return value.map(project);
}
function reason(value: unknown): MatchReason {
  const v = object(value);
  return {
    code: member(v.code, matchReasonCodes),
    component: member(v.component, matchComponents),
    value: nullableText(v.value),
  };
}
function explanationItem(value: unknown): MatchExplanationItem {
  const v = object(value);
  return {
    code: member(v.code, explanationCodes),
    source: member(v.source, ["profile", "job", "comparison"]),
    value: nullableText(v.value),
  };
}
function component(value: unknown): MatchComponent {
  const v = object(value);
  return {
    weight: number(v.weight, 0, 100),
    score: v.score === null ? null : number(v.score, 0, 100),
    status: member(v.status, ["matched", "partial", "mismatch", "unknown"]),
    matched: list(v.matched, text),
    missing: list(v.missing, text),
  };
}
function components<T>(
  value: unknown,
  project: (item: unknown) => T,
): Record<MatchComponentName, T> {
  const v = object(value);
  return Object.fromEntries(
    matchComponents.map((name) => [name, project(v[name])]),
  ) as Record<MatchComponentName, T>;
}
// Validation and allowlist projection only; all matching decisions stay in the API.
export function projectMatchResult(
  value: unknown,
  applicationId: number,
): MatchResult {
  const v = object(value);
  const state = object(v.input_state);
  const recommendation = object(v.recommendation);
  const result: MatchResult = {
    algorithm_version: member(v.algorithm_version, ["job-match-v2.1", "job-match-v2.2"]),
    application_id: number(v.application_id, 1, Number.MAX_SAFE_INTEGER),
    job_id: number(v.job_id, 1, Number.MAX_SAFE_INTEGER),
    score: v.score === null ? null : number(v.score, 0, 100),
    verdict: member(v.verdict, ["high", "medium", "low", "insufficient_data"]),
    coverage: number(v.coverage, 0, 100),
    confidence:
      v.confidence === null
        ? null
        : member(v.confidence, ["high", "medium", "low"]),
    input_state: {
      profile_updated_at: timestamp(state.profile_updated_at),
      job_updated_at: timestamp(state.job_updated_at),
      parsing_status: member(state.parsing_status, [
        "not_attempted",
        "pending",
        "success",
        "partial",
        "failed",
      ]),
      ai_enrichment_status: member(state.ai_enrichment_status, [
        "not_attempted",
        "pending",
        "success",
        "failed",
      ]),
    },
    components: components(v.components, component),
    strengths: list(v.strengths, reason),
    gaps: list(v.gaps, reason),
    unknowns: list(v.unknowns, reason),
    conflicts: list(v.conflicts, reason),
    recommendation: {
      code: member(recommendation.code, [
        "apply",
        "apply_with_risks",
        "unlikely_fit",
        "insufficient_data",
      ]),
      primary_reason:
        recommendation.primary_reason === null
          ? null
          : reason(recommendation.primary_reason),
    },
  };
  if (
    result.application_id !== applicationId ||
    (result.verdict === "insufficient_data"
      ? result.score !== null || result.confidence !== null
      : result.score === null || result.confidence === null)
  )
    return invalid();
  if (v.explainability != null) {
    const explanation = object(v.explainability);
    if (explanation.schema_version !== 1) return invalid();
    result.explainability = {
      schema_version: 1,
      components: components(explanation.components, (item) => {
        const e = object(item);
        return {
          notices: list(e.notices, explanationItem),
          unknown_items: list(e.unknown_items, explanationItem),
        };
      }),
    };
  }
  return result;
}
