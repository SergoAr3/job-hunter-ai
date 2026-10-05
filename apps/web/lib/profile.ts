export const experienceLevels = [
  "intern",
  "junior",
  "middle",
  "senior",
  "lead",
  "unknown",
] as const;
export type ExperienceLevel = (typeof experienceLevels)[number];
export const workplacePreferences = [
  "remote",
  "hybrid",
  "onsite",
  "any",
] as const;
export type WorkplacePreference = (typeof workplacePreferences)[number];
export const salaryPeriods = ["month", "year", "unknown"] as const;
export type SalaryPeriod = (typeof salaryPeriods)[number];
export const languageLevels = [
  "A1",
  "A2",
  "B1",
  "B2",
  "C1",
  "C2",
  "fluent",
  "native",
] as const;
export type LanguageLevel = (typeof languageLevels)[number];

export const experienceLabels: Record<ExperienceLevel, string> = {
  intern: "Стажёр",
  junior: "Junior",
  middle: "Middle",
  senior: "Senior",
  lead: "Lead",
  unknown: "Не указан",
};
export const workplaceLabels: Record<WorkplacePreference, string> = {
  remote: "Удалённо",
  hybrid: "Гибрид",
  onsite: "На месте",
  any: "Любой",
};
export const periodLabels: Record<SalaryPeriod, string> = {
  month: "в месяц",
  year: "в год",
  unknown: "Не указан",
};

export interface ProfileInput {
  target_roles: string[];
  skills: string[];
  experience: ExperienceLevel;
  location: string[];
  workplace_preference: WorkplacePreference;
  salary_min: string | null;
  salary_currency: string | null;
  salary_period: SalaryPeriod;
  languages: { language: string; level: LanguageLevel }[];
}
export interface ProfileDraft extends Omit<ProfileInput, "languages"> {
  languages: { language: string; level: string }[];
}
export interface Profile extends ProfileDraft {
  created_at: string;
  updated_at: string;
}
export interface WorkExperience {
  company: string | null;
  position: string | null;
  engagement_kind: "employment" | "internship" | "freelance" | "unknown";
  start_year: number | null;
  start_month: number | null;
  end_year: number | null;
  end_month: number | null;
  is_current: boolean | null;
  duration_months: number | null;
}
export interface WorkExperienceEntry extends WorkExperience {
  id: number;
}
export interface ExperienceFact {
  text: string;
}

export type ProfileField = keyof ProfileInput;
export type ProfileFieldErrors = Partial<Record<ProfileField, string>>;
const profileKeys: ProfileField[] = [
  "target_roles",
  "skills",
  "experience",
  "location",
  "workplace_preference",
  "salary_min",
  "salary_currency",
  "salary_period",
  "languages",
];
export const profileFieldNames: Record<ProfileField, string> = {
  target_roles: "Желаемые роли",
  skills: "Навыки",
  experience: "Уровень опыта",
  location: "Локации",
  workplace_preference: "Формат работы",
  salary_min: "Сумма",
  salary_currency: "Валюта",
  salary_period: "Период",
  languages: "Языки",
};

export const emptyProfile: ProfileInput = {
  target_roles: [],
  skills: [],
  experience: "unknown",
  location: [],
  workplace_preference: "any",
  salary_min: null,
  salary_currency: null,
  salary_period: "unknown",
  languages: [],
};

export function profileDraft(profile: Profile | null): ProfileDraft {
  return profile
    ? {
        target_roles: [...profile.target_roles],
        skills: [...profile.skills],
        experience: profile.experience,
        location: [...profile.location],
        workplace_preference: profile.workplace_preference,
        salary_min: profile.salary_min,
        salary_currency: profile.salary_currency,
        salary_period: profile.salary_period,
        languages: profile.languages.map((item) => ({ ...item })),
      }
    : {
        ...emptyProfile,
        target_roles: [],
        skills: [],
        location: [],
        languages: [],
      };
}

// Mirrors the API's active ISO 4217 codes for immediate form feedback.
const salaryCurrencies = new Set(
  "AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAD XAF XCD XDR XOF XPF XSU XUA YER ZAR ZMW ZWG".split(
    " ",
  ),
);
const workplaceLocations = new Set([
  "any",
  "remote",
  "hybrid",
  "onsite",
  "любой",
  "удаленно",
  "гибрид",
  "на месте работодателя",
]);
function isWorkplaceLocation(value: string): boolean {
  return workplaceLocations.has(
    value
      .toLocaleLowerCase()
      .replaceAll("ё", "е")
      .trim()
      .split(/\s+/)
      .join(" "),
  );
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function validateProfileInput(
  value: unknown,
  allowEmptyRoles = false,
): {
  payload: ProfileInput | null;
  errors: ProfileFieldErrors;
  invalidShape: boolean;
} {
  if (
    !record(value) ||
    Object.keys(value).length !== profileKeys.length ||
    Object.keys(value).some((key) => !profileKeys.includes(key as ProfileField))
  ) {
    return { payload: null, errors: {}, invalidShape: true };
  }
  const source: Record<string, unknown> = value;
  const errors: ProfileFieldErrors = {};
  function stringList(
    key: "target_roles" | "skills" | "location",
    required = false,
  ): string[] {
    const raw = source[key];
    if (
      !Array.isArray(raw) ||
      raw.length > 30 ||
      raw.some((item) => typeof item !== "string")
    ) {
      errors[key] = "Допустимо не более 30 текстовых значений.";
      return [];
    }
    const items = raw.map((item: string) => item.trim());
    if (
      (required && !allowEmptyRoles && !items.length) ||
      (required && items.some((item) => !item)) ||
      (key === "location" && items.some((item) => !item)) ||
      items.some((item) => item.length > 100)
    ) {
      errors[key] = required
        ? "Укажите хотя бы одну роль; каждое название — до 100 символов."
        : "Каждое значение должно содержать не более 100 символов.";
    }
    return key === "skills" ? items.filter(Boolean) : items;
  }
  const target_roles = stringList("target_roles", true);
  const skills = stringList("skills");
  const location = stringList("location");
  if (location.some(isWorkplaceLocation))
    errors.location =
      "Для формата работы используйте поле «Формат работы», а в локации укажите географическое место.";
  const experience = value.experience;
  if (!experienceLevels.includes(experience as ExperienceLevel))
    errors.experience = "Выберите допустимый уровень опыта.";
  const workplace_preference = value.workplace_preference;
  if (
    !workplacePreferences.includes(workplace_preference as WorkplacePreference)
  )
    errors.workplace_preference = "Выберите допустимый формат работы.";
  const rawLanguages = value.languages;
  let languages: ProfileInput["languages"] = [];
  if (
    !Array.isArray(rawLanguages) ||
    rawLanguages.length > 30 ||
    rawLanguages.some(
      (item) =>
        !record(item) ||
        Object.keys(item).length !== 2 ||
        !("language" in item) ||
        !("level" in item) ||
        typeof item.language !== "string" ||
        !languageLevels.includes(item.level as LanguageLevel),
    )
  ) {
    errors.languages =
      "Укажите язык и допустимый уровень; не более 30 записей.";
  } else {
    languages = rawLanguages.map((item) => ({
      language: (item.language as string).trim(),
      level: item.level as LanguageLevel,
    }));
    const names = languages.map((item) => item.language.toLocaleLowerCase());
    if (
      languages.some((item) => !item.language || item.language.length > 100) ||
      new Set(names).size !== names.length
    )
      errors.languages =
        "Укажите разные языки; название каждого — до 100 символов.";
  }
  const amount = value.salary_min;
  const currency = value.salary_currency;
  const period = value.salary_period;
  const absent = amount === null || amount === "";
  if (
    !absent &&
    (typeof amount !== "string" ||
      !/^\d{1,12}(?:\.\d{1,2})?$/.test(amount) ||
      !/[1-9]/.test(amount))
  )
    errors.salary_min =
      "Укажите положительную сумму с точностью до двух знаков.";
  if (
    currency !== null &&
    (typeof currency !== "string" ||
      !salaryCurrencies.has(currency.toUpperCase()))
  )
    errors.salary_currency = "Укажите действующий код валюты.";
  if (!salaryPeriods.includes(period as SalaryPeriod))
    errors.salary_period = "Выберите период.";
  if (
    absent &&
    ((currency !== null && currency !== "") || period !== "unknown")
  )
    errors.salary_min = "Укажите сумму или очистите весь зарплатный блок.";
  if (!absent && (!currency || period === "unknown")) {
    if (!currency) errors.salary_currency = "Укажите валюту.";
    if (period === "unknown") errors.salary_period = "Выберите месяц или год.";
  }
  if (Object.keys(errors).length)
    return { payload: null, errors, invalidShape: false };
  return {
    payload: {
      target_roles,
      skills,
      experience: experience as ExperienceLevel,
      location,
      workplace_preference: workplace_preference as WorkplacePreference,
      salary_min: absent ? null : (amount as string),
      salary_currency: absent ? null : (currency as string).toUpperCase(),
      salary_period: period as SalaryPeriod,
      languages,
    },
    errors: {},
    invalidShape: false,
  };
}

export function isProfileField(value: string): value is ProfileField {
  return profileKeys.includes(value as ProfileField);
}
