import type {
  MatchExplanationItem,
  MatchComponentName,
  MatchResult,
  MatchStatus,
  MatchVerdict,
} from "./match-contract";
export const matchVerdictLabels: Record<MatchVerdict, string> = {
  high: "Высокое соответствие",
  medium: "Среднее соответствие",
  low: "Низкое соответствие",
  insufficient_data: "Недостаточно данных",
};
export const matchStatusLabels: Record<MatchStatus, string> = {
  matched: "Совпадает",
  partial: "Частичное совпадение",
  mismatch: "Есть расхождение",
  unknown: "Недостаточно данных",
};
export const matchStatusIcons: Record<MatchStatus, string> = {
  matched: "✓",
  partial: "◐",
  mismatch: "!",
  unknown: "?",
};
export const explanationLabels: Record<MatchExplanationItem["code"], string> = {
  job_skills_missing: "В данных вакансии нет списка навыков для сравнения.",
  job_skills_unavailable:
    "Требования к навыкам вакансии недоступны для сравнения.",
  profile_skills_missing: "Добавьте навыки в профиль для сравнения.",
  job_languages_missing:
    "В данных вакансии нет языковых требований для сравнения.",
  job_languages_unavailable:
    "Языковые требования вакансии недоступны для сравнения.",
  language_requirement_unparseable:
    "Требование не удалось распознать для сравнения.",
  profile_language_level_unknown:
    "Уровень языка в профиле не удалось сравнить с требованием.",
  job_role_missing: "В данных вакансии нет названия роли.",
  profile_roles_missing: "Добавьте целевые роли в профиль.",
  job_seniority_unknown: "Уровень роли вакансии не определён.",
  profile_seniority_unknown: "Укажите уровень в профиле.",
  job_workplace_unknown: "Формат работы вакансии не определён.",
  workplace_any: "Формат допустим по вашим предпочтениям: любой формат.",
  remote_location_not_compared:
    "Локация не сравнивалась для удалённого формата.",
  location_not_compared: "Локация не сравнивалась: формат работы не определён.",
  job_location_missing: "В данных вакансии нет локации для сравнения.",
  profile_locations_missing: "Добавьте предпочтительные локации в профиль.",
  job_salary_missing: "В вакансии не указаны суммы зарплаты для сравнения.",
  profile_salary_missing: "Добавьте минимальные зарплатные ожидания в профиль.",
  job_salary_currency_missing: "Валюта зарплаты вакансии не указана.",
  profile_salary_currency_missing:
    "Валюта зарплатных ожиданий не указана в профиле.",
  salary_currencies_incompatible:
    "Валюты различаются; конвертация не выполняется.",
  job_salary_period_unknown: "Период зарплаты вакансии неизвестен.",
  profile_salary_period_unknown:
    "Укажите период зарплатных ожиданий в профиле.",
  salary_period_inferred:
    "Период зарплаты вакансии определён предположительно и требует проверки.",
  salary_not_comparable: "Нет данных для надёжного сравнения зарплаты.",
};

export const matchCriteria: [MatchComponentName, string][] = [
  ["role", "Роль"],
  ["seniority", "Уровень"],
  ["languages", "Языки"],
  ["workplace", "Формат работы"],
  ["location", "Локация"],
  ["salary", "Зарплата"],
];

export type MatchPointer = {
  target: string;
  text: string;
  kind: "gap" | "mismatch" | "partial" | "unknown";
};

// Shared presentation of already evaluated facts; never calculates matching.
export function getMatchPointers(
  result: MatchResult,
  prefix: string,
  includePartial = true,
): MatchPointer[] {
  const pointers: MatchPointer[] = [];
  const required = result.components.required_skills;
  if (required.missing.length)
    pointers.push({
      target: `${prefix}-required`,
      kind: "gap",
      text: `Не найдено обязательных навыков в профиле: ${required.missing.length}`,
    });
  const concerns = matchCriteria.filter(
    ([name]) =>
      result.components[name].status === "mismatch" ||
      result.conflicts.some((reason) => reason.component === name),
  );
  const partial = matchCriteria.filter(
    ([name]) =>
      result.components[name].status === "partial" &&
      !concerns.some(([other]) => name === other),
  );
  const unknownCriteria = matchCriteria.filter(([name]) => {
    const explanation = result.explainability?.components[name];
    return (
      (result.components[name].status === "unknown" ||
        !!explanation?.unknown_items.length) &&
      !explanation?.notices.some((item) =>
        ["remote_location_not_compared", "location_not_compared"].includes(
          item.code,
        ),
      )
    );
  });
  const hasUnknown =
    required.status === "unknown" || unknownCriteria.length > 0;
  const room = 4 - pointers.length - (hasUnknown ? 1 : 0);
  for (const [name, label] of [
    ...concerns,
    ...(includePartial ? partial : []),
  ].slice(0, room))
    pointers.push({
      target: `${prefix}-criteria-${name}`,
      kind: concerns.some(([other]) => name === other) ? "mismatch" : "partial",
      text:
        name === "role"
          ? result.components.role.status === "partial"
            ? "Роль: проверьте частичное совпадение"
            : "Роль расходится с целевыми ролями профиля"
          : `${label}: проверьте расхождение с предпочтениями`,
    });
  if (hasUnknown)
    pointers.push(
      required.status === "unknown"
        ? {
            target: `${prefix}-required`,
            kind: "unknown",
            text: result.explainability?.components.required_skills.notices.some(
              (item) => item.source === "profile",
            )
              ? "Заполните навыки профиля для сравнения"
              : "Требования к обязательным навыкам требуют уточнения",
          }
        : {
            target: `${prefix}-criteria`,
            kind: "unknown",
            text: unknownCriteria.some(
              ([name]) =>
                result.explainability?.components[name].notices.some(
                  (item) => item.source === "profile",
                ) ||
                result.explainability?.components[name].unknown_items.some(
                  (item) => item.source === "profile",
                ),
            )
              ? "Уточните данные профиля и условия вакансии для сравнения"
              : "Недостаточно данных по условиям вакансии",
          },
    );
  return pointers;
}

export function getMatchPreviewRows(
  result: MatchResult,
): { text: string; icon: string }[] {
  const pointers = getMatchPointers(
    result,
    `match-${result.application_id}`,
    false,
  );
  const rows = pointers.map(({ text, kind }) => ({
    text,
    icon: kind === "unknown" ? "?" : "!",
  }));
  if (!pointers.some(({ kind }) => kind === "gap" || kind === "mismatch")) {
    if (
      result.components.required_skills.status === "matched" &&
      result.components.required_skills.matched.length
    )
      rows.push({
        text: "Обязательные навыки совпадают с профилем",
        icon: "✓",
      });
    if (result.components.role.status === "matched")
      rows.push({
        text: "Роль соответствует целевым ролям профиля",
        icon: "✓",
      });
  }
  return rows.slice(0, 4);
}
