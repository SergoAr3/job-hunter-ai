"use client";

import Link from "next/link";
import { WorkExperienceList } from "./work-experience-editor";
import { CVDocumentIcon } from "./cv-document-icon";

import { useEffect, useRef, useState } from "react";
import { webRequest } from "../lib/client";
import { errorMessage, WebError } from "../lib/errors";
import { ListboxSelect } from "./listbox-select";
import { appendTag, TagEditor } from "./tag-editor";
import {
  experienceLabels,
  experienceLevels,
  languageLevels,
  periodLabels,
  profileDraft,
  profileFieldNames,
  salaryPeriods,
  validateProfileInput,
  workplaceLabels,
  workplacePreferences,
  type ExperienceFact,
  type Profile,
  type ProfileDraft,
  type ProfileField,
  type ProfileFieldErrors,
  type WorkExperienceEntry,
} from "../lib/profile";

const fields: ProfileField[] = [
  "target_roles",
  "skills",
  "experience",
  "languages",
  "location",
  "workplace_preference",
  "salary_min",
  "salary_currency",
  "salary_period",
];
const languageLevelLabels = Object.fromEntries(
  languageLevels.map((level) => [level, level]),
) as Record<(typeof languageLevels)[number], string>;

function valueList(items: string[], empty: string) {
  return items.length ? (
    <ul className="profile-chips">
      {items.map((item, index) => (
        <li key={`${item}-${index}`}>{item}</li>
      ))}
    </ul>
  ) : (
    <p className="profile-empty">{empty}</p>
  );
}

function ProfileRead({ profile }: { profile: Profile }) {
  return (
    <div className="profile-sections">
      <section
        className="profile-section"
        aria-labelledby="profile-roles-heading"
      >
        <h2 id="profile-roles-heading">Желаемые роли</h2>
        {valueList(profile.target_roles, "Роли не указаны.")}
      </section>
      <section
        className="profile-section"
        aria-labelledby="profile-skills-heading"
      >
        <h2 id="profile-skills-heading">Навыки</h2>
        {valueList(profile.skills, "Навыки не указаны.")}
      </section>
      <section
        className="profile-section"
        aria-labelledby="profile-experience-heading"
      >
        <h2 id="profile-experience-heading">Опыт</h2>
        <dl className="profile-details">
          <div>
            <dt>Уровень опыта</dt>
            <dd>{experienceLabels[profile.experience]}</dd>
          </div>
        </dl>
      </section>
      <section
        className="profile-section"
        aria-labelledby="profile-languages-heading"
      >
        <h2 id="profile-languages-heading">Языки</h2>
        {profile.languages.length ? (
          <ul className="profile-inline-list">
            {profile.languages.map((item, index) => (
              <li key={`${item.language}-${index}`}>
                <strong>{item.language}</strong> · {item.level}
              </li>
            ))}
          </ul>
        ) : (
          <p className="profile-empty">Языки не указаны.</p>
        )}
      </section>
      <section
        className="profile-section"
        aria-labelledby="profile-preferences-heading"
      >
        <h2 id="profile-preferences-heading">Предпочтения</h2>
        <dl className="profile-details">
          <div>
            <dt>Локации</dt>
            <dd>
              {profile.location.length
                ? profile.location.join(", ")
                : "Не указаны"}
            </dd>
          </div>
          <div>
            <dt>Формат работы</dt>
            <dd>{workplaceLabels[profile.workplace_preference]}</dd>
          </div>
          <div>
            <dt>Зарплата от</dt>
            <dd>
              {profile.salary_min === null
                ? "Не указана"
                : `${profile.salary_min} ${profile.salary_currency} ${periodLabels[profile.salary_period]}`}
            </dd>
          </div>
        </dl>
      </section>
    </div>
  );
}

function ProfileForm({
  initial,
  onCancel,
  onSave,
  pending,
  errors,
  formError,
}: {
  initial: ProfileDraft;
  onCancel: () => void;
  onSave: (value: ProfileDraft) => void;
  pending: boolean;
  errors: ProfileFieldErrors;
  formError: string;
}) {
  const [draft, setDraft] = useState<ProfileDraft>(initial);
  const [roleInput, setRoleInput] = useState("");
  const [skillInput, setSkillInput] = useState("");
  const error = (field: ProfileField) =>
    errors[field] ? (
      <p className="profile-field-error" id={`profile-${field}-error`}>
        {errors[field]}
      </p>
    ) : null;
  const invalid = (field: ProfileField) => (errors[field] ? true : undefined);
  const described = (field: ProfileField) =>
    errors[field] ? `profile-${field}-error` : undefined;
  function updateList(
    field: "target_roles" | "skills" | "location",
    index: number,
    value: string,
  ) {
    setDraft((old) => ({
      ...old,
      [field]: old[field].map((item, i) => (i === index ? value : item)),
    }));
  }
  function removeList(
    field: "target_roles" | "skills" | "location",
    index: number,
  ) {
    setDraft((old) => ({
      ...old,
      [field]: old[field].filter((_, i) => i !== index),
    }));
  }
  function listEditor(
    field: "target_roles" | "skills" | "location",
    label: string,
  ) {
    const itemLabel = field === "location" ? "локацию" : label.toLowerCase();
    return (
      <div className="profile-field-group">
        <div className="profile-field-heading">
          <span>{label}</span>
          <span className="profile-count">{draft[field].length}/30</span>
        </div>
        <div className="profile-repeatables">
          {draft[field].map((item, index) => (
            <div className="profile-repeat-row" key={index}>
              <label className="sr-only" htmlFor={`profile-${field}-${index}`}>
                {label}, запись {index + 1}
              </label>
              <input
                id={`profile-${field}-${index}`}
                value={item}
                maxLength={100}
                disabled={pending}
                aria-invalid={invalid(field)}
                aria-describedby={described(field)}
                onChange={(event) =>
                  updateList(field, index, event.target.value)
                }
              />
              <button
                type="button"
                disabled={pending}
                className="profile-remove"
                aria-label={`Удалить ${itemLabel}${item.trim() ? ` ${item.trim()}` : `, запись ${index + 1}`}`}
                onClick={() => removeList(field, index)}
              >
                <span aria-hidden="true">×</span>
              </button>
            </div>
          ))}
        </div>
        {error(field)}
        <button
          type="button"
          className="profile-add"
          disabled={pending || draft[field].length >= 30}
          onClick={() =>
            setDraft((old) => ({ ...old, [field]: [...old[field], ""] }))
          }
        >
          + Добавить {itemLabel}
        </button>
      </div>
    );
  }
  return (
    <form
      className="profile-form"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        const completed = {
          ...draft,
          target_roles: appendTag(draft.target_roles, roleInput, 30),
          skills: appendTag(draft.skills, skillInput, 30),
        };
        setDraft(completed);
        setRoleInput("");
        setSkillInput("");
        onSave(completed);
      }}
    >
      <section className="profile-section">
        <h2>Желаемые роли</h2>
        <p className="profile-hint">Укажите хотя бы одну роль.</p>
        <TagEditor
          id="profile-target_roles-input"
          label="Роль"
          itemName="роль"
          values={draft.target_roles}
          inputValue={roleInput}
          onInputChange={setRoleInput}
          onChange={(target_roles) =>
            setDraft((old) => ({ ...old, target_roles }))
          }
          maxItems={30}
          maxLength={100}
          placeholder="Добавить роль…"
          error={errors.target_roles}
          disabled={pending}
        />
      </section>
      <section className="profile-section">
        <h2>Навыки</h2>
        <TagEditor
          id="profile-skills-input"
          label="Навык"
          itemName="навык"
          values={draft.skills}
          inputValue={skillInput}
          onInputChange={setSkillInput}
          onChange={(skills) => setDraft((old) => ({ ...old, skills }))}
          maxItems={30}
          maxLength={100}
          placeholder="Добавить навык…"
          error={errors.skills}
          disabled={pending}
        />
      </section>
      <section className="profile-section">
        <h2>Опыт</h2>
        <label id="profile-experience-label" htmlFor="profile-experience">
          Уровень опыта
        </label>
        <ListboxSelect
          id="profile-experience"
          labelId="profile-experience-label"
          value={draft.experience}
          options={experienceLevels}
          labels={experienceLabels}
          disabled={pending}
          invalid={invalid("experience")}
          describedBy={described("experience")}
          onChange={(experience) => setDraft((old) => ({ ...old, experience }))}
        />
        {error("experience")}
      </section>
      <section className="profile-section">
        <h2>Языки</h2>
        <div className="profile-repeatables">
          {draft.languages.map((item, index) => (
            <div className="profile-language-row" key={index}>
              <div>
                <label htmlFor={`profile-language-${index}`}>
                  Язык {index + 1}
                </label>
                <input
                  id={`profile-language-${index}`}
                  value={item.language}
                  maxLength={100}
                  disabled={pending}
                  aria-invalid={invalid("languages")}
                  aria-describedby={described("languages")}
                  onChange={(event) =>
                    setDraft((old) => ({
                      ...old,
                      languages: old.languages.map((entry, i) =>
                        i === index
                          ? { ...entry, language: event.target.value }
                          : entry,
                      ),
                    }))
                  }
                />
              </div>
              <div>
                <label
                  id={`profile-level-${index}-label`}
                  htmlFor={`profile-level-${index}`}
                >
                  Уровень {index + 1}
                </label>
                <ListboxSelect<string>
                  id={`profile-level-${index}`}
                  labelId={`profile-level-${index}-label`}
                  value={item.level}
                  options={
                    languageLevels.includes(
                      item.level as (typeof languageLevels)[number],
                    )
                      ? languageLevels
                      : [item.level, ...languageLevels]
                  }
                  labels={
                    languageLevels.includes(
                      item.level as (typeof languageLevels)[number],
                    )
                      ? languageLevelLabels
                      : {
                          ...languageLevelLabels,
                          [item.level]: `Прежний уровень: ${item.level || "не указан"}`,
                        }
                  }
                  disabled={pending}
                  invalid={invalid("languages")}
                  describedBy={described("languages")}
                  onChange={(level) =>
                    setDraft((old) => ({
                      ...old,
                      languages: old.languages.map((entry, i) =>
                        i === index ? { ...entry, level } : entry,
                      ),
                    }))
                  }
                />
                {!languageLevels.includes(
                  item.level as (typeof languageLevels)[number],
                ) && (
                  <p className="profile-hint">
                    Выберите поддерживаемый уровень перед сохранением.
                  </p>
                )}
              </div>
              <button
                type="button"
                disabled={pending}
                className="profile-remove"
                aria-label={`Удалить язык${item.language.trim() ? ` ${item.language.trim()}` : `, запись ${index + 1}`}`}
                onClick={() =>
                  setDraft((old) => ({
                    ...old,
                    languages: old.languages.filter((_, i) => i !== index),
                  }))
                }
              >
                <span aria-hidden="true">×</span>
              </button>
            </div>
          ))}
        </div>
        {error("languages")}
        <button
          type="button"
          className="profile-add"
          disabled={pending || draft.languages.length >= 30}
          onClick={() =>
            setDraft((old) => ({
              ...old,
              languages: [...old.languages, { language: "", level: "A1" }],
            }))
          }
        >
          + Добавить язык
        </button>
      </section>
      <section className="profile-section">
        <h2>Предпочтения</h2>
        {listEditor("location", "Локация")}
        <div className="profile-field-group">
          <label
            id="profile-workplace_preference-label"
            htmlFor="profile-workplace_preference"
          >
            Формат работы
          </label>
          <ListboxSelect
            id="profile-workplace_preference"
            labelId="profile-workplace_preference-label"
            value={draft.workplace_preference}
            options={workplacePreferences}
            labels={workplaceLabels}
            disabled={pending}
            invalid={invalid("workplace_preference")}
            describedBy={described("workplace_preference")}
            onChange={(workplace_preference) =>
              setDraft((old) => ({ ...old, workplace_preference }))
            }
          />
          {error("workplace_preference")}
        </div>
        <fieldset className="profile-salary">
          <legend>Зарплата от</legend>
          <div className="profile-salary-fields">
            <div>
              <label htmlFor="profile-salary_min">Сумма</label>
              <input
                id="profile-salary_min"
                type="text"
                inputMode="decimal"
                value={draft.salary_min ?? ""}
                disabled={pending}
                aria-invalid={invalid("salary_min")}
                aria-describedby={described("salary_min")}
                onChange={(event) =>
                  setDraft({ ...draft, salary_min: event.target.value || null })
                }
              />
              {error("salary_min")}
            </div>
            <div>
              <label htmlFor="profile-salary_currency">Валюта</label>
              <input
                id="profile-salary_currency"
                type="text"
                maxLength={3}
                placeholder="USD"
                value={draft.salary_currency ?? ""}
                disabled={pending}
                aria-invalid={invalid("salary_currency")}
                aria-describedby={described("salary_currency")}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    salary_currency: event.target.value || null,
                  })
                }
              />
              {error("salary_currency")}
            </div>
            <div>
              <label
                id="profile-salary_period-label"
                htmlFor="profile-salary_period"
              >
                Период
              </label>
              <ListboxSelect
                id="profile-salary_period"
                labelId="profile-salary_period-label"
                value={draft.salary_period}
                options={salaryPeriods}
                labels={periodLabels}
                disabled={pending}
                invalid={invalid("salary_period")}
                describedBy={described("salary_period")}
                onChange={(salary_period) =>
                  setDraft((old) => ({ ...old, salary_period }))
                }
              />
              {error("salary_period")}
            </div>
          </div>
          <button
            type="button"
            className="profile-add"
            disabled={pending}
            onClick={() =>
              setDraft({
                ...draft,
                salary_min: null,
                salary_currency: null,
                salary_period: "unknown",
              })
            }
          >
            Очистить зарплату
          </button>
        </fieldset>
      </section>
      {formError && (
        <div
          className="notice error"
          role="alert"
          tabIndex={-1}
          id="profile-form-error"
        >
          {formError}
        </div>
      )}
      <div className="profile-actions">
        <button type="submit" className="primary" disabled={pending}>
          Сохранить
        </button>
        <button type="button" onClick={onCancel} disabled={pending}>
          Отмена
        </button>
      </div>
      {pending && <p role="status">Сохраняем профиль…</p>}
    </form>
  );
}

export function ProfileWorkspace() {
  const [confirmed, setConfirmed] = useState<Profile | null | undefined>(
    undefined,
  );
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [pending, setPending] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [pageError, setPageError] = useState("");
  const [formError, setFormError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<ProfileFieldErrors>({});
  const [feedback, setFeedback] = useState("");
  const [work, setWork] = useState<WorkExperienceEntry[] | null>(null);
  const [facts, setFacts] = useState<ExperienceFact[] | null>(null);
  const [workError, setWorkError] = useState(false);
  const [factsError, setFactsError] = useState(false);
  const [listsLoading, setListsLoading] = useState(false);
  const mounted = useRef(true);
  const locked = useRef(false);
  const initialLoadStarted = useRef(false);

  async function loadLists() {
    setListsLoading(true);
    const [workResult, factsResult] = await Promise.allSettled([
      webRequest<{ items: WorkExperienceEntry[] }>(
        "/api/profile/work-experiences",
      ),
      webRequest<{ items: ExperienceFact[] }>("/api/profile/experience-facts"),
    ]);
    if (!mounted.current) return;
    if (
      workResult.status === "fulfilled" &&
      Array.isArray(workResult.value.items)
    ) {
      setWork(workResult.value.items);
      setWorkError(false);
    } else setWorkError(true);
    if (
      factsResult.status === "fulfilled" &&
      Array.isArray(factsResult.value.items)
    ) {
      setFacts(factsResult.value.items);
      setFactsError(false);
    } else setFactsError(true);
    setListsLoading(false);
  }

  async function refresh(afterMutation = false) {
    setPending(true);
    setPageError("");
    try {
      const fresh = await webRequest<Profile>("/api/profile");
      if (!mounted.current) return;
      setConfirmed(fresh);
      setUncertain(false);
      setEditing(false);
      setDraft(null);
      setFeedback(afterMutation ? "Профиль сохранён и подтверждён." : "");
      if (work === null && facts === null) void loadLists();
    } catch (error) {
      if (!mounted.current) return;
      if (
        error instanceof WebError &&
        error.code === "profile_missing" &&
        !afterMutation
      ) {
        setConfirmed(null);
        setWork(null);
        setFacts(null);
        setUncertain(false);
        setEditing(false);
        setDraft(null);
        setFeedback("Профиль ещё не создан.");
      } else if (afterMutation || uncertain) {
        setUncertain(true);
        setEditing(false);
        setDraft(null);
        setPageError(errorMessage(new WebError("profile_unconfirmed")));
      } else setPageError(errorMessage(error));
    } finally {
      if (mounted.current) setPending(false);
    }
  }

  useEffect(() => {
    mounted.current = true;
    if (!initialLoadStarted.current) {
      initialLoadStarted.current = true;
      void Promise.resolve().then(() => {
        if (mounted.current) void refresh();
      });
    }
    return () => {
      mounted.current = false;
    };
    // Initial load belongs to this mount; later reads use the explicit Refresh action.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function beginEdit() {
    setDraft(profileDraft(confirmed ?? null));
    setFieldErrors({});
    setFormError("");
    setFeedback("");
    setEditing(true);
  }
  function cancel() {
    setDraft(null);
    setFieldErrors({});
    setFormError("");
    setEditing(false);
  }
  async function save(value: ProfileDraft) {
    if (locked.current) return;
    const result = validateProfileInput(value);
    if (!result.payload) {
      setFieldErrors(result.errors);
      setFormError("Проверьте отмеченные поля профиля.");
      const first = fields.find((field) => result.errors[field]);
      if (first)
        document
          .getElementById(
            `profile-${first === "target_roles" || first === "skills" ? `${first}-input` : first === "location" ? "location-0" : first === "languages" ? "language-0" : first}`,
          )
          ?.focus();
      return;
    }
    locked.current = true;
    setPending(true);
    setFormError("");
    setFieldErrors({});
    setFeedback("");
    try {
      await webRequest<{ ok: true }>("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(result.payload),
      });
      if (!mounted.current) return;
      await refresh(true);
    } catch (error) {
      if (!mounted.current) return;
      if (error instanceof WebError && error.code === "profile_invalid") {
        const safe: ProfileFieldErrors = {};
        for (const field of fields) {
          const message = error.fieldErrors?.[field];
          if (typeof message === "string")
            safe[field] = profileFieldNames[field]
              ? message
              : "Проверьте поле.";
        }
        setFieldErrors(safe);
        setFormError(errorMessage(error));
      } else if (
        error instanceof WebError &&
        error.code === "profile_user_not_found"
      ) {
        setFormError(errorMessage(error));
      } else {
        setUncertain(true);
        setEditing(false);
        setDraft(null);
        setPageError(errorMessage(new WebError("ambiguous_profile")));
      }
    } finally {
      locked.current = false;
      if (mounted.current) setPending(false);
    }
  }

  return (
    <div className="profile-page">
      <div className="profile-header page-heading">
        <div>
          <span className="eyebrow">ВАШ ПРОФИЛЬ</span>
          <h1>Профиль</h1>
          <p>Эти данные используются для оценки совпадения с вакансиями.</p>
        </div>
        <div className="profile-header-actions">
          {confirmed && !editing && !uncertain && (
            <button type="button" onClick={beginEdit}>
              Редактировать
            </button>
          )}
        </div>
      </div>
      {!editing && (
        <section
          className="profile-import-card"
          aria-labelledby="profile-import-heading"
        >
          <span className="cv-document-icon">
            <CVDocumentIcon />
          </span>
          <div>
            <h2 id="profile-import-heading">Заполнить профиль из резюме</h2>
            <p>
              Загрузите PDF или DOCX — мы извлечём данные и покажем изменения
              перед сохранением.
            </p>
          </div>
          <Link className="button-link" href="/profile/import">
            Импортировать резюме
          </Link>
        </section>
      )}
      {feedback && !editing && !uncertain && confirmed !== undefined && (
        <p className="profile-feedback" role="status">
          {feedback}
        </p>
      )}
      {pageError && (
        <div className="notice error" role="alert">
          <p>{pageError}</p>
          <button
            type="button"
            disabled={pending}
            onClick={() => void refresh()}
          >
            Обновить данные
          </button>
        </div>
      )}
      {confirmed === undefined && !pageError && (
        <p className="state" role="status">
          Загружаем профиль…
        </p>
      )}
      {uncertain && confirmed !== undefined && (
        <p className="profile-hint">
          Ниже показаны последние подтверждённые данные.
        </p>
      )}
      {confirmed === null && !editing && !uncertain && (
        <div className="profile-empty-state">
          <h2>Профиль ещё не создан</h2>
          <p>Добавьте желаемую роль и другие сведения для оценки вакансий.</p>
          <button type="button" className="primary" onClick={beginEdit}>
            Создать профиль
          </button>
        </div>
      )}
      {editing && draft && (
        <ProfileForm
          initial={draft}
          onCancel={cancel}
          onSave={(value) => void save(value)}
          pending={pending}
          errors={fieldErrors}
          formError={formError}
        />
      )}
      {confirmed && !editing && (
        <>
          <ProfileRead profile={confirmed} />
          <div className="profile-readonly-grid">
            <section
              className="profile-section"
              aria-labelledby="profile-work-heading"
            >
              <h2 id="profile-work-heading">Опыт работы</h2>
              {listsLoading && work === null ? (
                <p role="status">Загружаем опыт работы…</p>
              ) : workError ? (
                <div className="notice error" role="alert">
                  Не удалось загрузить опыт работы.{" "}
                  <button type="button" onClick={() => void loadLists()}>
                    Повторить
                  </button>
                </div>
              ) : work !== null ? (
                <WorkExperienceList
                  items={work}
                  onChange={(update) =>
                    setWork((current) => update(current ?? []))
                  }
                  disabled={uncertain}
                />
              ) : null}
            </section>
            <section
              className="profile-section"
              aria-labelledby="profile-facts-heading"
            >
              <h2 id="profile-facts-heading">Практический опыт</h2>
              {listsLoading && facts === null ? (
                <p role="status">Загружаем практический опыт…</p>
              ) : factsError ? (
                <div className="notice error" role="alert">
                  Не удалось загрузить практический опыт.{" "}
                  <button type="button" onClick={() => void loadLists()}>
                    Повторить
                  </button>
                </div>
              ) : facts?.length ? (
                <ul className="profile-facts">
                  {facts.map((fact, index) => (
                    <li key={index}>{fact.text}</li>
                  ))}
                </ul>
              ) : (
                <p className="profile-empty">
                  Практический опыт пока не добавлен.
                </p>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
