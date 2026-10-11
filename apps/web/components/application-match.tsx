"use client";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { webRequest } from "../lib/client";
import { WebError } from "../lib/errors";
import {
  projectMatchResult,
  type MatchResult,
  type MatchComponent,
  type MatchComponentExplanation,
  type MatchExplanationItem,
} from "../lib/match-contract";
import {
  explanationLabels,
  getMatchPointers,
  matchCriteria,
  matchStatusIcons,
  matchStatusLabels,
} from "../lib/match-presentation";
import { CompactMatchSummary, MatchSummary } from "./match-summary";
import { MatchDisclosure } from "./match-disclosure";

function SkillList({
  values,
  label,
  preferred = false,
  hints = {},
  status = "matched",
}: {
  values: string[];
  label: string;
  preferred?: boolean;
  hints?: Record<string, string>;
  status?: "matched" | "missing";
}) {
  const [expanded, setExpanded] = useState(false);
  if (!values.length) return null;
  return (
    <div
      className={`match-skill-group${preferred && status === "missing" ? " match-preferred-gap" : ""}`}
    >
      <p className="match-group-label">
        {label} <span>· {values.length}</span>
      </p>
      <ul role="list" aria-label={label}>
        {(expanded ? values : values.slice(0, 6)).map((value, index) => (
          <li
            role="listitem"
            className={`match-skill-row match-skill-${status}${preferred ? " match-skill-neutral" : ""}`}
            key={index}
          >
            <span className="match-skill-icon" aria-hidden="true">
              {status === "matched" ? "✓" : preferred ? "—" : "!"}
            </span>
            <div>
              <span>{value}</span>
              {Object.hasOwn(hints, value) && (
                <small className="match-helper">{hints[value]}</small>
              )}
            </div>
          </li>
        ))}
      </ul>
      {values.length > 6 && (
        <button
          type="button"
          className="text-action"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "Свернуть список" : `Показать все (${values.length})`}
        </button>
      )}
    </div>
  );
}
function Notices({
  explanation,
  required = false,
}: {
  explanation?: MatchComponentExplanation;
  required?: boolean;
}) {
  if (!explanation?.notices.length) return null;
  const summary =
    required &&
    explanation.notices.some((item) => item.code === "job_skills_missing")
      ? "В данных вакансии нет списка обязательных навыков для сравнения."
      : explanation.notices.some(
            (item) => item.code === "profile_skills_missing",
          )
        ? "Для сравнения навыков заполните профиль."
        : "Недостаточно данных для сравнения.";
  return (
    <div className="match-notice-summary">
      <p className="match-helper">{summary}</p>
      <MatchDisclosure label="Подробнее">
        {explanation.notices.map((notice, index) => (
          <p className="match-helper" key={index}>
            {explanationLabels[notice.code]}
          </p>
        ))}
      </MatchDisclosure>
    </div>
  );
}

function UnknownItems({
  items,
  notices = [],
}: {
  items: MatchExplanationItem[];
  notices?: MatchExplanationItem[];
}) {
  const [expanded, setExpanded] = useState(false);
  if (!items.length) return null;
  return (
    <div className="match-unknown-items">
      <p className="match-group-label">
        Не удалось определить <span>· {items.length}</span>
      </p>
      <ul role="list" aria-label="Не удалось определить">
        {(expanded ? items : items.slice(0, 6)).map((item, index) => (
          <li
            role="listitem"
            className="match-skill-row match-skill-unknown"
            key={index}
          >
            <span className="match-skill-icon" aria-hidden="true">
              ?
            </span>
            <div>
              {item.value && <span>{item.value}</span>}
              {!notices.some((notice) => notice.code === item.code) && (
                <small>{explanationLabels[item.code]}</small>
              )}
            </div>
          </li>
        ))}
      </ul>
      {items.length > 6 && (
        <button
          type="button"
          className="text-action"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "Свернуть список" : `Показать все (${items.length})`}
        </button>
      )}
    </div>
  );
}
function Skills({
  component,
  explanation,
  preferred = false,
  id,
}: {
  component: MatchComponent;
  explanation?: MatchComponentExplanation;
  preferred?: boolean;
  id: string;
}) {
  const unknownItems = explanation?.unknown_items ?? [];
  return (
    <section
      className={`match-skills${preferred ? " match-preferred" : ""}`}
      aria-labelledby={id}
    >
      <h3 id={id} tabIndex={-1}>
        {preferred ? "Желательные навыки" : "Обязательные навыки"}
      </h3>
      <SkillList
        values={component.matched}
        label="Совпадает"
        preferred={preferred}
      />
      <SkillList
        values={component.missing}
        label="Не найдено в профиле"
        preferred={preferred}
        status="missing"
      />
      {!unknownItems.length && !!explanation?.notices.length && (
        <p className="match-group-label">Не удалось определить</p>
      )}
      <Notices explanation={explanation} required={!preferred} />
      <UnknownItems items={unknownItems} notices={explanation?.notices} />
      {component.status === "unknown" &&
        !explanation?.notices.length &&
        !unknownItems.length && (
          <p className="match-helper">
            Не удалось определить: недостаточно данных для сравнения навыков.
          </p>
        )}
      {!preferred && (
        <p className="match-helper">
          Не найдено означает, что навык не указан в профиле. Это не
          подтверждает отсутствие навыка.
        </p>
      )}
    </section>
  );
}
const levels: Record<string, string> = {
  intern: "Intern",
  junior: "Junior",
  middle: "Middle",
  senior: "Senior",
  lead: "Lead",
};
const workplace: Record<string, string> = {
  remote: "Удалённо",
  onsite: "В офисе",
  hybrid: "Гибрид",
};
function SalaryNotes({
  explanation,
}: {
  explanation?: MatchComponentExplanation;
}) {
  if (!explanation?.notices.length) return null;
  const notices = explanation.notices;
  const summaries: string[] = [];
  if (notices.some((item) => item.source === "profile"))
    summaries.push("В профиле не указаны полные зарплатные ожидания.");
  if (notices.some((item) => item.code === "job_salary_missing"))
    summaries.push("В вакансии не указаны суммы зарплаты.");
  else if (
    notices.some((item) =>
      ["job_salary_period_unknown", "salary_period_inferred"].includes(
        item.code,
      ),
    )
  )
    summaries.push("Период выплаты вакансии требует уточнения.");
  else if (notices.some((item) => item.code === "job_salary_currency_missing"))
    summaries.push("Валюта зарплаты вакансии неизвестна.");
  if (notices.some((item) => item.code === "salary_currencies_incompatible"))
    summaries.push("Валюты различаются; конвертация не выполняется.");
  return (
    <div className="match-notice-summary">
      <p className="match-helper">Зарплату пока нельзя сравнить.</p>
      {summaries.slice(0, 2).map((summary) => (
        <p className="match-helper" key={summary}>
          {summary}
        </p>
      ))}
      <MatchDisclosure label="Подробнее">
        {notices.map((notice, index) => (
          <p className="match-helper" key={index}>
            {explanationLabels[notice.code]}
          </p>
        ))}
      </MatchDisclosure>
    </div>
  );
}
function Criteria({ result, id }: { result: MatchResult; id: string }) {
  return (
    <section aria-labelledby={id}>
      <h3 id={id} tabIndex={-1}>
        Другие критерии
      </h3>
      <p className="match-helper">
        Уровень сравнивается по уровню роли, без оценки стажа в годах.
      </p>
      <dl className="match-criteria">
        {matchCriteria.map(([name, label]) => {
          const component = result.components[name];
          const explanation = result.explainability?.components[name];
          const notCompared =
            name === "location" &&
            explanation?.notices.some((notice) =>
              [
                "remote_location_not_compared",
                "location_not_compared",
              ].includes(notice.code),
            );
          return (
            <div className="match-criterion" key={name}>
              <dt id={`${id}-${name}`} tabIndex={-1}>
                {label}
              </dt>
              <dd>
                {notCompared ? (
                  <p className="match-component-status match-status-unknown">
                    <span aria-hidden="true">—</span> Не сравнивалась
                  </p>
                ) : (
                  <p
                    className={`match-component-status match-status-${component.status}`}
                  >
                    <span aria-hidden="true">
                      {matchStatusIcons[component.status]}
                    </span>{" "}
                    {matchStatusLabels[component.status]}
                    {component.status !== "unknown" &&
                    explanation?.unknown_items.length
                      ? " по сравнимым требованиям"
                      : ""}
                  </p>
                )}
                {name === "languages" ? (
                  <>
                    <SkillList
                      values={component.matched}
                      label="Уровень соответствует требованию"
                    />
                    <SkillList
                      values={component.missing}
                      label="Есть расхождение с требованием"
                      status="missing"
                      hints={Object.fromEntries(
                        result.gaps
                          .filter(
                            (reason) =>
                              reason.component === "languages" &&
                              reason.value !== null &&
                              component.missing.includes(reason.value) &&
                              [
                                "language_not_listed",
                                "language_level_below_requirement",
                              ].includes(reason.code),
                          )
                          .map((reason) => [
                            reason.value!,
                            reason.code === "language_level_below_requirement"
                              ? "Уровень в профиле ниже требуемого."
                              : "Язык не указан в профиле.",
                          ]),
                      )}
                    />
                    {(!!explanation?.unknown_items.length ||
                      !!explanation?.notices.length) && (
                      <div className="match-notice-summary">
                        <p className="match-helper">
                          {component.status === "unknown"
                            ? "Языковые требования пока нельзя сравнить."
                            : "Часть языковых требований не удалось сравнить."}
                        </p>
                        <MatchDisclosure label="Подробнее">
                          <UnknownItems
                            items={explanation?.unknown_items ?? []}
                          />
                          {explanation?.notices
                            .filter(
                              (notice) =>
                                !explanation.unknown_items.some(
                                  (item) =>
                                    item.code === notice.code &&
                                    item.source === notice.source,
                                ),
                            )
                            .map((notice, index) => (
                              <p className="match-helper" key={index}>
                                {explanationLabels[notice.code]}
                              </p>
                            ))}
                        </MatchDisclosure>
                      </div>
                    )}
                  </>
                ) : name === "role" && component.status === "mismatch" ? (
                  <>
                    <p className="match-detail-copy">
                      Целевая роль вакансии не совпадает с указанными в профиле.
                    </p>
                    {!!component.missing.length && (
                      <MatchDisclosure label="Показать роли профиля">
                        <ul className="match-role-list">
                          {component.missing.map((role, index) => (
                            <li key={index}>{role}</li>
                          ))}
                        </ul>
                      </MatchDisclosure>
                    )}
                  </>
                ) : (
                  (component.matched.length > 0 ||
                    component.missing.length > 0) && (
                    <p className="match-detail-copy">
                      {name === "role" && component.status === "mismatch"
                        ? "Целевые роли профиля: "
                        : name === "seniority"
                          ? "Требуемый уровень: "
                          : ""}
                      {[...component.matched, ...component.missing]
                        .map((value) =>
                          name === "seniority"
                            ? (levels[value] ?? value)
                            : name === "workplace"
                              ? (workplace[value] ?? value)
                              : value,
                        )
                        .join(" · ")}
                    </p>
                  )
                )}
                {name === "salary" && component.status === "partial" && (
                  <p className="match-helper">
                    Верхняя граница зарплаты достигает ожиданий; нижняя не
                    подтверждает соответствие минимуму.
                  </p>
                )}
                {name === "salary" && component.status === "mismatch" && (
                  <p className="match-helper">
                    Верхняя граница зарплаты ниже вашего минимума.
                  </p>
                )}
                {name === "salary" ? (
                  <SalaryNotes explanation={explanation} />
                ) : (
                  name !== "languages" &&
                  (explanation?.notices.length === 1 ? (
                    <p className="match-helper">
                      {explanationLabels[explanation.notices[0].code]}
                    </p>
                  ) : (
                    <Notices explanation={explanation} />
                  ))
                )}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}
function CheckPointers({
  result,
  prefix,
}: {
  result: MatchResult;
  prefix: string;
}) {
  const pointers = getMatchPointers(result, prefix);
  if (!pointers.length) return null;
  return (
    <section aria-labelledby={`${prefix}-checks`}>
      <h3 id={`${prefix}-checks`}>Что проверить</h3>
      <ul role="list" className="match-checks">
        {pointers.map((pointer) => (
          <li key={pointer.target}>
            <a
              href={`#${pointer.target}`}
              onClick={() => document.getElementById(pointer.target)?.focus()}
            >
              {pointer.text}
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
export function MatchFacts({
  result,
  includeSummary = true,
}: {
  result: MatchResult;
  includeSummary?: boolean;
}) {
  const prefix = `match-${result.application_id}`;
  return (
    <>
      {includeSummary && <MatchSummary {...result} />}
      {result.input_state.ai_enrichment_status !== "success" && (
        <p className="match-helper">
          Данные о навыках и языковых требованиях вакансии недоступны для
          сравнения. Остальные критерии показаны по доступным данным.
        </p>
      )}
      {result.input_state.parsing_status === "partial" && (
        <p className="match-helper">
          Данные вакансии извлечены частично; проверьте условия у источника.
        </p>
      )}
      {result.input_state.parsing_status === "failed" && (
        <p className="match-helper">
          Не удалось полностью получить данные вакансии; проверьте условия у
          источника.
        </p>
      )}
      <div className="match-skill-columns">
        <Skills
          id={`${prefix}-required`}
          component={result.components.required_skills}
          explanation={result.explainability?.components.required_skills}
        />
        <Skills
          id={`${prefix}-preferred`}
          component={result.components.nice_to_have_skills}
          explanation={result.explainability?.components.nice_to_have_skills}
          preferred
        />
      </div>
      <Criteria result={result} id={`${prefix}-criteria`} />
      <CheckPointers result={result} prefix={prefix} />
      <Link className="text-link match-profile-link" href="/profile">
        Редактировать профиль →
      </Link>
    </>
  );
}
type State =
  | { kind: "loading" }
  | { kind: "ready"; result: MatchResult }
  | { kind: "profile" }
  | { kind: "missing" }
  | { kind: "error" };
type MatchSlots = { summary: ReactNode; details: ReactNode };
type MatchProps = {
  applicationId: number;
  children?: (slots: MatchSlots) => ReactNode;
};
function MatchLoader({ applicationId, children }: MatchProps) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void webRequest<unknown>(`/api/applications/${applicationId}/match`, {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(35000)]),
    })
      .then((value) => {
        if (!controller.signal.aborted)
          setState({
            kind: "ready",
            result: projectMatchResult(value, applicationId),
          });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          kind:
            error instanceof WebError && error.code === "PROFILE_REQUIRED"
              ? "profile"
              : error instanceof WebError &&
                  error.code === "APPLICATION_NOT_FOUND"
                ? "missing"
                : "error",
        });
      });
    return () => controller.abort();
  }, [applicationId, attempt]);
  // A restored browser page must not treat an old result as current.
  useEffect(() => {
    function refresh(event: PageTransitionEvent) {
      if (event.persisted) {
        setState({ kind: "loading" });
        setAttempt((current) => current + 1);
      }
    }
    window.addEventListener("pageshow", refresh);
    return () => window.removeEventListener("pageshow", refresh);
  }, []);
  const summary = (
    <section
      className="match-compact"
      aria-labelledby={`match-${applicationId}-heading`}
      aria-busy={state.kind === "loading"}
    >
      <h2 id={`match-${applicationId}-heading`}>Соответствие профилю</h2>
      {state.kind === "loading" && (
        <div className="match-loading" role="status">
          <span className="match-skeleton" aria-hidden="true" />
          Сравниваем с профилем…
        </div>
      )}
      {state.kind === "ready" && <CompactMatchSummary result={state.result} />}
      {state.kind === "profile" && (
        <div className="match-empty">
          <p>Для сравнения нужен профиль.</p>
          <div className="match-actions">
            <Link className="button-link" href="/profile">
              Редактировать профиль
            </Link>
            <Link className="button-link" href="/profile/import">
              Импортировать CV
            </Link>
          </div>
        </div>
      )}
      {state.kind === "missing" && (
        <p role="alert">
          Сохранённая вакансия больше недоступна.{" "}
          <Link href="/applications">К моим вакансиям</Link>
        </p>
      )}
      {state.kind === "error" && (
        <div role="alert" className="match-error">
          <p>Не удалось загрузить оценку.</p>
          <button
            type="button"
            onClick={() => {
              setState({ kind: "loading" });
              setAttempt((current) => current + 1);
            }}
          >
            Повторить
          </button>
        </div>
      )}
    </section>
  );
  const details =
    state.kind === "ready" ? (
      <section className="application-match" aria-labelledby="match-details">
        <h2 id="match-details" tabIndex={-1}>
          Подробное соответствие профилю
        </h2>
        <MatchDisclosure label="Как считается оценка">
          <p className="match-helper">
            Критерии имеют разный вес. Неизвестные данные исключаются из оценки.
          </p>
        </MatchDisclosure>
        <MatchFacts result={state.result} includeSummary={false} />
      </section>
    ) : null;
  return children ? (
    children({ summary, details })
  ) : (
    <>
      {summary}
      {details}
    </>
  );
}
export function ApplicationMatch(props: MatchProps) {
  return <MatchLoader key={props.applicationId} {...props} />;
}
