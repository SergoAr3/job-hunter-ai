import "server-only";
import { readWorkExperience } from "./work-experiences";
import { getDomainIdentity } from "./auth";
import { WebError } from "../errors";
import {
  isProfileField,
  profileFieldNames,
  experienceLevels,
  workplacePreferences,
  salaryPeriods,
  type Profile,
  type ProfileInput,
  type WorkExperienceEntry,
  type ExperienceFact,
  type ProfileFieldErrors,
  type ProfileField,
} from "../profile";

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readProfile(value: unknown): Profile {
  if (!record(value)) throw new WebError("api_unavailable");
  const stringList = (items: unknown): items is string[] =>
    Array.isArray(items) && items.every((item) => typeof item === "string");
  if (
    !stringList(value.target_roles) ||
    !stringList(value.skills) ||
    !stringList(value.location) ||
    !experienceLevels.includes(value.experience as Profile["experience"]) ||
    !workplacePreferences.includes(
      value.workplace_preference as Profile["workplace_preference"],
    ) ||
    !salaryPeriods.includes(value.salary_period as Profile["salary_period"]) ||
    !(value.salary_min === null || typeof value.salary_min === "string") ||
    !(
      value.salary_currency === null ||
      typeof value.salary_currency === "string"
    ) ||
    !Array.isArray(value.languages) ||
    !value.languages.every(
      (item: unknown) =>
        record(item) &&
        typeof item.language === "string" &&
        typeof item.level === "string",
    ) ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string"
  )
    throw new WebError("api_unavailable");
  // The API keeps legacy language strings readable; PUT validation stays strict.
  return {
    target_roles: value.target_roles,
    skills: value.skills,
    experience: value.experience as Profile["experience"],
    location: value.location,
    workplace_preference:
      value.workplace_preference as Profile["workplace_preference"],
    salary_min: value.salary_min,
    salary_currency: value.salary_currency,
    salary_period: value.salary_period as Profile["salary_period"],
    languages: value.languages.map(
      (item: { language: string; level: string }) => ({
        language: item.language,
        level: item.level,
      }),
    ),
    created_at: value.created_at,
    updated_at: value.updated_at,
  };
}

async function upstream(
  path: "profile" | "profile/work-experiences" | "profile/experience-facts",
  method: "GET" | "PUT" = "GET",
  payload?: ProfileInput,
): Promise<unknown> {
  const { userId, baseUrl, headers } = await getDomainIdentity();
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/users/${userId}/${path}`, {
      method,
      cache: "no-store",
      redirect: "error",
      headers: {
        ...headers,
        ...(payload ? { "Content-Type": "application/json" } : {}),
      },
      body: payload ? JSON.stringify(payload) : undefined,
      signal: AbortSignal.timeout(method === "PUT" ? 60000 : 35000),
    });
  } catch {
    throw new WebError(
      method === "PUT" ? "ambiguous_profile" : "api_unavailable",
      503,
    );
  }
  if (response.status === 401) throw new WebError("unauthenticated", 401);
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    throw new WebError(
      method === "PUT" ? "ambiguous_profile" : "api_unavailable",
      502,
    );
  }
  if (!response.ok) {
    if (response.status === 404)
      throw new WebError(
        method === "PUT" ? "profile_user_not_found" : "profile_missing",
        404,
      );
    if (response.status === 422 && method === "PUT")
      throw new WebError("profile_invalid", 422, upstreamFieldErrors(value));
    if (method === "PUT" && response.status >= 500)
      throw new WebError("ambiguous_profile", response.status);
    throw new WebError("api_unavailable", response.status);
  }
  return value;
}

function upstreamFieldErrors(value: unknown): ProfileFieldErrors {
  const errors: ProfileFieldErrors = {};
  if (!record(value) || !Array.isArray(value.detail)) return errors;
  for (const item of value.detail) {
    if (!record(item) || !Array.isArray(item.loc)) continue;
    const key = item.loc.find(
      (part): part is ProfileField =>
        typeof part === "string" && isProfileField(part),
    );
    if (key) errors[key] = `Проверьте поле «${profileFieldNames[key]}».`;
  }
  return errors;
}

export async function getProfile(): Promise<Profile> {
  return readProfile(await upstream("profile"));
}

export async function putProfile(payload: ProfileInput): Promise<{ ok: true }> {
  try {
    readProfile(await upstream("profile", "PUT", payload));
  } catch (error) {
    if (error instanceof WebError && error.code === "api_unavailable")
      throw new WebError("ambiguous_profile", error.status);
    throw error;
  }
  return { ok: true };
}

export async function getWorkExperiences(): Promise<{
  items: WorkExperienceEntry[];
}> {
  const value = await upstream("profile/work-experiences");
  if (!record(value) || !Array.isArray(value.items))
    throw new WebError("api_unavailable");
  return { items: value.items.map(readWorkExperience) };
}

export async function getExperienceFacts(): Promise<{
  items: ExperienceFact[];
}> {
  const value = await upstream("profile/experience-facts");
  if (!record(value) || !Array.isArray(value.items))
    throw new WebError("api_unavailable");
  return {
    items: value.items.map((item): ExperienceFact => {
      if (!record(item) || typeof item.text !== "string")
        throw new WebError("api_unavailable");
      return { text: item.text };
    }),
  };
}
