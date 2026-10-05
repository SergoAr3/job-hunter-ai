import type { WorkExperience } from "./profile";

export const engagementKinds = [
  "employment",
  "internship",
  "freelance",
  "unknown",
] as const;
export const engagementLabels: Record<
  WorkExperience["engagement_kind"],
  string
> = {
  employment: "Работа по найму",
  internship: "Стажировка",
  freelance: "Фриланс",
  unknown: "Не указан",
};
export const workFields = [
  "company",
  "position",
  "engagement_kind",
  "start_year",
  "start_month",
  "end_year",
  "end_month",
  "is_current",
] as const;
export type WorkInput = Omit<WorkExperience, "duration_months">;
export const workFieldLabels: Record<keyof WorkInput, string> = {
  company: "Компания / проект",
  position: "Должность",
  engagement_kind: "Тип занятости",
  start_year: "Год начала",
  start_month: "Месяц начала",
  end_year: "Год окончания",
  end_month: "Месяц окончания",
  is_current: "По настоящее время",
};
export const emptyWork: WorkInput = {
  company: null,
  position: null,
  engagement_kind: "unknown",
  start_year: null,
  start_month: null,
  end_year: null,
  end_month: null,
  is_current: null,
};

// Match only the whitespace normalization accepted by WorkExperienceIn.clean_text.
// Python split() includes these Unicode separators, but does not strip a BOM as
// JavaScript trim()/\s would. Control characters remain invalid API input.
function recoveryText(value: WorkInput[keyof WorkInput]) {
  return typeof value === "string"
    ? value
        .replace(
          /[ \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g,
          " ",
        )
        .replace(/^ | $/g, "") || null
    : value;
}

export function workFieldMatches(
  key: keyof WorkInput,
  stored: WorkInput,
  draft: WorkInput,
) {
  return key === "company" || key === "position"
    ? recoveryText(stored[key]) === recoveryText(draft[key])
    : stored[key] === draft[key];
}
