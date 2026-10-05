"use client";

import { ListboxSelect } from "./listbox-select";
import { engagementKinds, engagementLabels } from "../lib/work-experiences";

import { useEffect, useRef, useState } from "react";
import type { CVEdit, CVPreview } from "../lib/cv-import";
import {
  experienceLabels,
  experienceLevels,
  workplaceLabels,
  workplacePreferences,
  periodLabels,
  salaryPeriods,
  languageLevels,
} from "../lib/profile";

export type EditTarget =
  | { kind: "work" | "fact"; index: number }
  | { kind: "profile"; field: "main" | "skills" | "languages" };

function Tokens({
  label,
  values,
  onChange,
}: {
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
}) {
  const [text, setText] = useState("");
  function add() {
    if (text.trim()) {
      onChange([...values, text.trim()]);
      setText("");
    }
  }
  return (
    <div className="cv-token-editor">
      <span>{label}</span>
      <ul className="profile-chips">
        {values.map((value, index) => (
          <li key={index}>
            {value}
            <button
              type="button"
              aria-label={`Удалить ${value}`}
              onClick={() => onChange(values.filter((_, i) => i !== index))}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <div className="cv-token-add">
        <input
          aria-label={`Добавить: ${label}`}
          value={text}
          maxLength={200}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <button type="button" onClick={add}>
          Добавить
        </button>
      </div>
    </div>
  );
}

export function CVPreviewEditor({
  preview,
  target,
  pending,
  errors,
  message,
  onSave,
  onCancel,
  onDelete,
}: {
  preview: CVPreview;
  target: EditTarget;
  pending: boolean;
  errors: Record<string, string>;
  message: string;
  onSave: (edit: CVEdit) => void;
  onCancel: () => void;
  onDelete?: () => void;
}) {
  const form = useRef<HTMLFormElement>(null);
  const [work, setWork] = useState(
    target.kind === "work"
      ? { ...preview.work_experience[target.index] }
      : null,
  );
  const [fact, setFact] = useState(
    target.kind === "fact" ? preview.experience_facts[target.index] : "",
  );
  const [profile, setProfile] = useState({
    ...preview.proposed,
    languages: preview.proposed.languages.map((x) => ({ ...x })),
  });
  useEffect(() => {
    form.current?.querySelector<HTMLElement>("input,select,textarea")?.focus();
  }, []);
  const error = (field: string) => (
    <>
      {errors[field] && (
        <span className="field-error" id={`cv-error-${field}`}>
          {errors[field]}
        </span>
      )}
    </>
  );
  const aria = (field: string) => {
    const key = errors[field]
      ? field
      : /^(start|end)_(year|month)$/.test(field) && errors.dates
        ? "dates"
        : null;
    return {
      "aria-invalid": !!key,
      "aria-describedby": key ? `cv-error-${key}` : undefined,
    };
  };
  return (
    <form
      ref={form}
      className="cv-preview-editor"
      aria-label="Редактирование предпросмотра"
      onSubmit={(e) => {
        e.preventDefault();
        if (target.kind === "work" && work)
          onSave({ kind: "work", index: target.index, value: work });
        else if (target.kind === "fact")
          onSave({ kind: "fact", index: target.index, text: fact });
        else onSave({ kind: "profile", value: profile });
      }}
    >
      <fieldset disabled={pending}>
        {target.kind === "work" && work && (
          <>
            <label>
              Должность
              <input
                value={work.position ?? ""}
                maxLength={200}
                onChange={(e) =>
                  setWork({ ...work, position: e.target.value || null })
                }
                {...aria("position")}
              />
              {error("position")}
            </label>
            <label>
              Компания / проект
              <input
                value={work.company ?? ""}
                maxLength={200}
                onChange={(e) =>
                  setWork({ ...work, company: e.target.value || null })
                }
                {...aria("company")}
              />
              {error("company")}
            </label>
            <div className="cv-select-field">
              <label id="cv-engagement_kind-label" htmlFor="cv-engagement_kind">
                Тип занятости
              </label>
              <ListboxSelect
                id="cv-engagement_kind"
                labelId="cv-engagement_kind-label"
                value={work.engagement_kind}
                options={engagementKinds}
                labels={engagementLabels}
                disabled={pending}
                onChange={(value) =>
                  setWork({ ...work, engagement_kind: value })
                }
                invalid={Boolean(errors["engagement_kind"])}
                describedBy={aria("engagement_kind")["aria-describedby"]}
              />

              {error("engagement_kind")}
            </div>
            {(["start", "end"] as const).map((part) => (
              <div key={part} className="cv-date-fields">
                <span>{part === "start" ? "Начало" : "Окончание"}</span>
                {(["year", "month"] as const).map((unit) => {
                  const field = `${part}_${unit}` as
                    "start_year" | "start_month" | "end_year" | "end_month";
                  return (
                    <label key={field}>
                      {unit === "year" ? "Год" : "Месяц"}
                      <input
                        type="number"
                        aria-label={`${part === "start" ? "Начало" : "Окончание"}: ${unit === "year" ? "год" : "месяц"}`}
                        min={unit === "year" ? 1900 : 1}
                        max={unit === "year" ? 9999 : 12}
                        disabled={part === "end" && work.is_current === true}
                        value={work[field] ?? ""}
                        onChange={(e) =>
                          setWork({
                            ...work,
                            [field]: e.target.value
                              ? Number(e.target.value)
                              : null,
                            ...(part === "end" && e.target.value
                              ? { is_current: false }
                              : {}),
                          })
                        }
                        {...aria(field)}
                      />
                      {error(field)}
                    </label>
                  );
                })}
              </div>
            ))}
            <label className="cv-current-checkbox">
              <input
                type="checkbox"
                checked={work.is_current === true}
                onChange={(e) =>
                  setWork({
                    ...work,
                    is_current: e.target.checked,
                    ...(e.target.checked
                      ? { end_year: null, end_month: null }
                      : {}),
                  })
                }
              />
              По настоящее время
            </label>
            {error("dates")}
          </>
        )}
        {target.kind === "fact" && (
          <label>
            Факт практического опыта
            <textarea
              aria-label="Факт практического опыта"
              value={fact}
              required
              maxLength={500}
              rows={4}
              onChange={(e) => setFact(e.target.value)}
              {...aria("text")}
            />
            {error("text")}
          </label>
        )}
        {target.kind === "profile" && (
          <>
            <p className="dashboard-muted">
              Существующие списки профиля сохраняются по правилам импорта. Здесь
              исправляются предложения этого резюме.
            </p>
            {target.field === "main" && (
              <>
                <Tokens
                  label="Желаемые роли"
                  values={profile.target_roles}
                  onChange={(target_roles) =>
                    setProfile({ ...profile, target_roles })
                  }
                />
                {error("target_roles")}
                <div className="cv-select-field">
                  <label id="cv-experience-label" htmlFor="cv-experience">
                    Уровень опыта
                  </label>
                  <ListboxSelect
                    id="cv-experience"
                    labelId="cv-experience-label"
                    value={profile.experience}
                    options={experienceLevels}
                    labels={experienceLabels}
                    disabled={pending}
                    onChange={(value) =>
                      setProfile({ ...profile, experience: value })
                    }
                    invalid={Boolean(errors["experience"])}
                    describedBy={aria("experience")["aria-describedby"]}
                  />

                  {error("experience")}
                </div>
                <Tokens
                  label="Локации"
                  values={profile.location}
                  onChange={(location) => setProfile({ ...profile, location })}
                />
                {error("location")}
                <div className="cv-select-field">
                  <label
                    id="cv-workplace_preference-label"
                    htmlFor="cv-workplace_preference"
                  >
                    Формат работы
                  </label>
                  <ListboxSelect
                    id="cv-workplace_preference"
                    labelId="cv-workplace_preference-label"
                    value={profile.workplace_preference}
                    options={workplacePreferences}
                    labels={workplaceLabels}
                    disabled={pending}
                    onChange={(value) =>
                      setProfile({ ...profile, workplace_preference: value })
                    }
                    invalid={Boolean(errors["workplace_preference"])}
                    describedBy={
                      aria("workplace_preference")["aria-describedby"]
                    }
                  />

                  {error("workplace_preference")}
                </div>
                <label>
                  Зарплата от
                  <input
                    value={profile.salary_min ?? ""}
                    inputMode="decimal"
                    onChange={(e) =>
                      setProfile({
                        ...profile,
                        salary_min: e.target.value || null,
                      })
                    }
                    {...aria("salary_min")}
                  />
                  {error("salary_min")}
                </label>
                <label>
                  Валюта
                  <input
                    value={profile.salary_currency ?? ""}
                    maxLength={3}
                    onChange={(e) =>
                      setProfile({
                        ...profile,
                        salary_currency: e.target.value.toUpperCase() || null,
                      })
                    }
                    {...aria("salary_currency")}
                  />
                  {error("salary_currency")}
                </label>
                <div className="cv-select-field">
                  <label id="cv-salary_period-label" htmlFor="cv-salary_period">
                    Период
                  </label>
                  <ListboxSelect
                    id="cv-salary_period"
                    labelId="cv-salary_period-label"
                    value={profile.salary_period}
                    options={salaryPeriods}
                    labels={periodLabels}
                    disabled={pending}
                    onChange={(value) =>
                      setProfile({ ...profile, salary_period: value })
                    }
                    invalid={Boolean(errors["salary_period"])}
                    describedBy={aria("salary_period")["aria-describedby"]}
                  />

                  {error("salary_period")}
                </div>
              </>
            )}
            {target.field === "skills" && (
              <>
                <Tokens
                  label="Навыки"
                  values={profile.skills}
                  onChange={(skills) => setProfile({ ...profile, skills })}
                />
                {error("skills")}
              </>
            )}
            {target.field === "languages" && (
              <>
                <div className="cv-language-editor">
                  {profile.languages.map((item, index) => (
                    <div key={index}>
                      <label>
                        Язык
                        <input
                          value={item.language}
                          maxLength={100}
                          onChange={(e) =>
                            setProfile({
                              ...profile,
                              languages: profile.languages.map((x, i) =>
                                i === index
                                  ? { ...x, language: e.target.value }
                                  : x,
                              ),
                            })
                          }
                        />
                      </label>
                      <div className="cv-select-field">
                        <label
                          id={`cv-level-${index}-label`}
                          htmlFor={`cv-level-${index}`}
                        >
                          Уровень
                        </label>
                        <ListboxSelect<string>
                          id={`cv-level-${index}`}
                          labelId={`cv-level-${index}-label`}
                          value={item.level}
                          options={languageLevels}
                          labels={Object.fromEntries(
                            languageLevels.map((level) => [level, level]),
                          )}
                          disabled={pending}
                          editable
                          maxLength={100}
                          onChange={(value) =>
                            setProfile({
                              ...profile,
                              languages: profile.languages.map((x, i) =>
                                i === index ? { ...x, level: value } : x,
                              ),
                            })
                          }
                        />
                      </div>
                      <button
                        type="button"
                        aria-label={`Удалить язык ${item.language}`}
                        onClick={() =>
                          setProfile({
                            ...profile,
                            languages: profile.languages.filter(
                              (_, i) => i !== index,
                            ),
                          })
                        }
                      >
                        Удалить
                      </button>
                    </div>
                  ))}
                </div>

                <button
                  type="button"
                  onClick={() =>
                    setProfile({
                      ...profile,
                      languages: [
                        ...profile.languages,
                        { language: "", level: "" },
                      ],
                    })
                  }
                >
                  Добавить язык
                </button>
                {error("languages")}
              </>
            )}
          </>
        )}
        {message && (
          <p className="field-error" role="alert">
            {message}
          </p>
        )}
        <div className="cv-editor-actions">
          <button type="submit" className="primary">
            {pending ? "Сохраняем…" : "Сохранить"}
          </button>
          <button type="button" onClick={onCancel}>
            Отмена
          </button>
          {onDelete && (
            <button type="button" onClick={onDelete}>
              Удалить запись
            </button>
          )}
        </div>
      </fieldset>
    </form>
  );
}
