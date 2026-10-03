import type { ApplicationDetail, Preview, Reason } from "../lib/contracts";
import { compactLocation } from "../lib/presentation";
export { statusLabels } from "../lib/application-presentation";
export const workplace: Record<string, string> = {
  remote: "Удалённо",
  onsite: "В офисе",
  hybrid: "Гибрид",
};
export function Salary({
  job,
}: {
  job:
    | ApplicationDetail["job"]
    | Pick<
        ApplicationDetail["job"],
        "salary_text" | "salary_min" | "salary_max" | "salary_currency"
      >;
}) {
  const minimum =
    job.salary_min != null && job.salary_min > 0 ? job.salary_min : null;
  const maximum =
    job.salary_max != null && job.salary_max > 0 ? job.salary_max : null;
  const sourceText = job.salary_text?.trim();
  const sourceTextIsZero = sourceText
    ? /^(?:(?:от|до)\s*)?0+(?:[.,]0+)?\s*(?:(?:₽)|(?:RUB)|(?:руб(?:\.|лей|ля|ль)?))?$/i.test(
        sourceText,
      )
    : false;
  if (minimum == null && maximum == null) {
    return sourceText && !sourceTextIsZero ? <span>{sourceText}</span> : null;
  }
  const number = (value: number) =>
    new Intl.NumberFormat("ru-RU").format(value);
  return (
    <span title={job.salary_text ?? undefined}>
      {minimum != null && maximum != null
        ? minimum === maximum
          ? number(minimum)
          : `${number(minimum)} – ${number(maximum)}`
        : minimum != null
          ? `от ${number(minimum)}`
          : `до ${number(maximum!)}`}{" "}
      {job.salary_currency === "RUB" ? "₽" : job.salary_currency}
    </span>
  );
}
export function SourceLink({ url }: { url: string }) {
  try {
    if (!["https:", "http:"].includes(new URL(url).protocol)) return null;
  } catch {
    return null;
  }
  return (
    <a
      className="text-link"
      href={url}
      target="_blank"
      rel="noopener noreferrer"
    >
      Открыть у источника ↗
    </a>
  );
}
export function JobMetadata({ job }: { job: ApplicationDetail["job"] }) {
  const location = compactLocation(job.location);
  return (
    <>
      <div className="metadata">
        {location &&
          (location !== job.location ? (
            <details className="location-detail">
              <summary>{location}</summary>
              <p className="raw-location">{job.location}</p>
            </details>
          ) : (
            <span>{location}</span>
          ))}
        {workplace[job.workplace_type] && (
          <span className="workplace-label">
            {workplace[job.workplace_type]}
          </span>
        )}
      </div>
      <p className="salary detail-salary">
        <Salary job={job} />
      </p>
    </>
  );
}
function SourceText({ text }: { text: string }) {
  // Preserve the source's own paragraph boundaries and all original words.
  const paragraphs = (
    <div className="source-copy">
      {text
        .split(/\r?\n/)
        .filter((line) => line.trim())
        .map((line, index) => (
          <p className="source-text" key={index}>
            {line}
          </p>
        ))}
    </div>
  );
  if (text.length <= 900) return paragraphs;
  // A literal excerpt, not a summary. Full content remains available by keyboard.
  const boundary = text.lastIndexOf(" ", 460);
  const excerpt = text.slice(0, boundary > 0 ? boundary : 460);
  return (
    <details className="source-disclosure" key={text}>
      <summary>
        <span className="source-excerpt" aria-hidden="true">
          {excerpt}…
        </span>
        <span className="read-more">Читать полностью</span>
        <span className="read-less">Свернуть текст</span>
      </summary>
      {paragraphs}
    </details>
  );
}
export function JobText({ job }: { job: ApplicationDetail["job"] }) {
  return (
    <div className="job-content">
      {job.description && (
        <section>
          <h3>О вакансии</h3>
          <SourceText text={job.description} />
        </section>
      )}
      {job.requirements_text && (
        <section>
          <h3>Требования</h3>
          <SourceText text={job.requirements_text} />
        </section>
      )}
    </div>
  );
}
const reasonLabels: Record<string, string> = {
  role_matched: "Роль совпадает",
  role_partial: "Частичное совпадение роли",
  role_not_matched: "Роль не совпадает",
  vacancy_role_unknown: "Роль не указана",
  profile_target_roles_missing: "В профиле нет целевых ролей",
  required_skill_listed: "Необходимый навык указан",
  required_skill_not_listed: "Необходимый навык не указан",
  nice_to_have_skill_listed: "Дополнительный навык указан",
  nice_to_have_skill_not_listed: "Дополнительный навык не указан",
  vacancy_skills_unavailable: "Недостаточно данных о навыках вакансии",
  profile_skills_missing: "В профиле нет навыков",
  seniority_matches: "Уровень опыта совпадает",
  seniority_below_requirement: "Опыт ниже требований",
  vacancy_seniority_unknown: "Уровень вакансии не указан",
  profile_seniority_unknown: "Уровень профиля не указан",
  language_level_sufficient: "Уровень языка подходит",
  language_requirements_unparseable: "Требования к языкам не определены",
  language_not_listed: "Язык не указан в профиле",
  language_level_below_requirement: "Уровень языка ниже требований",
  vacancy_language_requirements_unavailable: "Нет данных о языках вакансии",
  workplace_matches: "Формат работы подходит",
  workplace_not_preferred: "Формат работы отличается от предпочтений",
  vacancy_workplace_unknown: "Формат работы не указан",
  location_matches: "Локация совпадает",
  location_not_listed: "Локация не указана в профиле",
  vacancy_location_missing: "Нет локации вакансии",
  profile_locations_missing: "Нет локаций профиля",
  salary_meets_expectations: "Зарплата соответствует ожиданиям",
  salary_below_minimum: "Зарплата ниже ожиданий",
  vacancy_salary_missing: "Зарплата вакансии не указана",
  profile_salary_missing: "Ожидания по зарплате не указаны",
  salary_period_inferred: "Период зарплаты определён предположительно",
  salary_not_comparable: "Зарплаты нельзя сопоставить",
};
function ReasonText({ reason }: { reason: Reason }) {
  return (
    <>
      {reasonLabels[reason.code] ?? "Дополнительное замечание API"}
      {reason.value ? `: ${reason.value}` : ""}
    </>
  );
}
export function Matching({ preview }: { preview: Preview }) {
  const verdicts: Record<string, string> = {
    high: "Высокое соответствие",
    medium: "Среднее соответствие",
    low: "Низкое соответствие",
    insufficient_data: "Недостаточно данных",
  };
  const recommendations: Record<string, string> = {
    apply: "Можно откликнуться",
    apply_with_risks: "Отклик с учётом рисков",
    unlikely_fit: "Вероятно, не подходит",
    insufficient_data: "Для рекомендации недостаточно данных",
  };
  return (
    <section
      className={`matching ${!preview.available || preview.verdict === "insufficient_data" ? "matching-muted" : ""}`}
    >
      <div className="section-heading">
        <h3>Соответствие профилю</h3>
        <span className="preview-label">Предварительная оценка</span>
      </div>
      {!preview.available ? (
        <div className="matching-unavailable">
          <p className="match-verdict">Оценка пока недоступна</p>
          <p>
            {preview.unavailable_reason === "profile_missing_for_preview"
              ? "Для оценки нужен профиль. Настройте его в Telegram — поиск и сохранение уже доступны."
              : "Предварительная оценка недоступна."}
          </p>
        </div>
      ) : (
        <>
          <p className="match-score">
            {preview.score != null && (
              <strong>
                {preview.score}
                <small>/100</small>{" "}
              </strong>
            )}
            {preview.verdict && (verdicts[preview.verdict] ?? "Оценка API")}
          </p>
          <div className="metadata">
            {preview.coverage != null && (
              <span>Для оценки доступно {preview.coverage}% данных.</span>
            )}
            {preview.confidence && (
              <span>
                Уверенность:{" "}
                {(
                  {
                    high: "высокая",
                    medium: "средняя",
                    low: "низкая",
                  } as Record<string, string>
                )[preview.confidence] ?? preview.confidence}
              </span>
            )}
          </div>
          <div className="reason-groups">
            {(
              [
                ["strengths", "Совпадения"],
                ["gaps", "Различия"],
                ["unknowns", "Неизвестно"],
                ["conflicts", "Противоречия"],
              ] as const
            ).map(
              ([key, label]) =>
                preview[key]?.length > 0 && (
                  <details key={key}>
                    <summary>
                      {label} · {preview[key].length}
                    </summary>
                    <ul>
                      {preview[key].map((reason, index) => (
                        <li key={index}>
                          <ReasonText reason={reason} />
                        </li>
                      ))}
                    </ul>
                  </details>
                ),
            )}
          </div>
          {preview.recommendation && (
            <p className="recommendation">
              {recommendations[preview.recommendation.code] ??
                "Рекомендация API"}
              {preview.recommendation.primary_reason && (
                <>
                  .{" "}
                  <ReasonText reason={preview.recommendation.primary_reason} />
                </>
              )}
            </p>
          )}
        </>
      )}
    </section>
  );
}
