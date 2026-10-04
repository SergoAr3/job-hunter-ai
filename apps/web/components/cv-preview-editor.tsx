"use client";

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
            <label>
              Тип занятости
              <select
                value={work.engagement_kind}
                onChange={(e) =>
                  setWork({
                    ...work,
                    engagement_kind: e.target
                      .value as typeof work.engagement_kind,
                  })
                }
                {...aria("engagement_kind")}
              >
                <option value="unknown">Не указан</option>
                <option value="employment">Работа</option>
                <option value="internship">Стажировка</option>
                <option value="freelance">Фриланс</option>
              </select>
              {error("engagement_kind")}
            </label>
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
                <label>
                  Уровень опыта
                  <select
                    value={profile.experience}
                    onChange={(e) =>
                      setProfile({
                        ...profile,
                        experience: e.target.value as typeof profile.experience,
                      })
                    }
                  >
                    {experienceLevels.map((x) => (
                      <option key={x} value={x}>
                        {experienceLabels[x]}
                      </option>
                    ))}
                  </select>
                  {error("experience")}
                </label>
                <Tokens
                  label="Локации"
                  values={profile.location}
                  onChange={(location) => setProfile({ ...profile, location })}
                />
                {error("location")}
                <label>
                  Формат работы
                  <select
                    value={profile.workplace_preference}
                    onChange={(e) =>
                      setProfile({
                        ...profile,
                        workplace_preference: e.target
                          .value as typeof profile.workplace_preference,
                      })
                    }
                  >
                    {workplacePreferences.map((x) => (
                      <option key={x} value={x}>
                        {workplaceLabels[x]}
                      </option>
                    ))}
                  </select>
                  {error("workplace_preference")}
                </label>
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
                <label>
                  Период
                  <select
                    value={profile.salary_period}
                    onChange={(e) =>
                      setProfile({
                        ...profile,
                        salary_period: e.target
                          .value as typeof profile.salary_period,
                      })
                    }
                  >
                    {salaryPeriods.map((x) => (
                      <option key={x} value={x}>
                        {periodLabels[x]}
                      </option>
                    ))}
                  </select>
                  {error("salary_period")}
                </label>
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
                      <label>
                        Уровень
                        <input
                          value={item.level}
                          list="cv-language-levels"
                          maxLength={100}
                          onChange={(e) =>
                            setProfile({
                              ...profile,
                              languages: profile.languages.map((x, i) =>
                                i === index
                                  ? { ...x, level: e.target.value }
                                  : x,
                              ),
                            })
                          }
                        />
                      </label>
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
                <datalist id="cv-language-levels">
                  {languageLevels.map((x) => (
                    <option key={x} value={x} />
                  ))}
                </datalist>
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
