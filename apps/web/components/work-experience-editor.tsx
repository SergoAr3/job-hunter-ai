"use client";

import { ListboxSelect } from "./listbox-select";
import { useEffect, useRef, useState } from "react";
import type { WorkExperienceEntry } from "../lib/profile";
import {
  emptyWork,
  engagementKinds,
  engagementLabels,
  workFields,
  workFieldLabels,
  workFieldMatches,
  type WorkInput,
} from "../lib/work-experiences";
import { webRequest } from "../lib/client";
import { errorMessage, WebError } from "../lib/errors";

function inputOf(entry?: WorkExperienceEntry): WorkInput {
  return entry
    ? (Object.fromEntries(
        workFields.map((key) => [key, entry[key]]),
      ) as WorkInput)
    : { ...emptyWork };
}
function period(year: number | null, month: number | null) {
  return year === null
    ? "Не указано"
    : month === null
      ? String(year)
      : `${String(month).padStart(2, "0")}.${year}`;
}

function Editor({
  entry,
  onSaved,
  onCancel,
}: {
  entry?: WorkExperienceEntry;
  onSaved: (entry: WorkExperienceEntry) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(() => inputOf(entry));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [uncertain, setUncertain] = useState(false);
  const [checking, setChecking] = useState(false);
  const [initial, setInitial] = useState(() => inputOf(entry));
  const form = useRef<HTMLFormElement>(null);
  const locked = useRef(false);
  const active = useRef(true);
  const cancelRequested = useRef(false);
  useEffect(() => {
    active.current = true;
    form.current?.querySelector<HTMLInputElement>("input")?.focus();
    return () => {
      active.current = false;
    };
  }, []);
  useEffect(() => {
    if (error)
      form.current?.querySelector<HTMLElement>("[role=alert]")?.focus();
  }, [error]);
  const changes = Object.fromEntries(
    workFields
      .filter((key) => draft[key] !== initial[key])
      .map((key) => [key, draft[key]]),
  );
  function change(key: keyof WorkInput, value: WorkInput[keyof WorkInput]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (locked.current || uncertain || !Object.keys(changes).length) return;
    locked.current = true;
    setPending(true);
    setError("");
    setErrors({});
    try {
      const result = await webRequest<WorkExperienceEntry>(
        `/api/profile/work-experiences${entry ? `/${entry.id}` : ""}`,
        {
          method: entry ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(entry ? changes : draft),
        },
      );
      // A sent mutation can finish after Profile switches to its other editor.
      // Keep the parent collection canonical even when this form has unmounted.
      onSaved(result);
    } catch (failure) {
      if (!active.current) return;
      if (cancelRequested.current) {
        onCancel();
        return;
      }
      setError(errorMessage(failure));
      if (failure instanceof WebError) {
        setErrors(failure.fieldErrors ?? {});
        setUncertain(
          failure.code === "work_unconfirmed" ||
            failure.code === "work_not_found",
        );
      }
    } finally {
      locked.current = false;
      if (active.current) setPending(false);
    }
  }
  async function check() {
    setChecking(true);
    try {
      const { items } = await webRequest<{ items: WorkExperienceEntry[] }>(
        "/api/profile/work-experiences",
      );
      if (!active.current) return;
      const match = entry
        ? items.find((item) => item.id === entry.id)
        : items.find((item) =>
            workFields.every((key) => workFieldMatches(key, item, draft)),
          );
      if (
        match &&
        workFields.every(
          (key) => !(key in changes) || workFieldMatches(key, match, draft),
        )
      ) {
        onSaved(match);
        return;
      }
      if (entry && !match) {
        setError(
          "Запись удалена или заменена импортом. Отмените редактирование и обновите список.",
        );
        return;
      }
      if (match) {
        // Preserve local changed fields while refreshing untouched fields. A
        // read after an uncertain PATCH must not turn old values into changes.
        setInitial(inputOf(match));
        setDraft({ ...inputOf(match), ...changes });
      }
      setUncertain(false);
      setError(
        "Актуальные данные проверены. Черновик сохранён; проверьте его перед сохранением.",
      );
    } catch (failure) {
      if (active.current) setError(errorMessage(failure));
    } finally {
      if (active.current) setChecking(false);
    }
  }
  function aria(key: keyof WorkInput) {
    return {
      id: `work-${key}`,
      "aria-invalid": !!errors[key],
      "aria-describedby": errors[key]
        ? `work-error-${key}`
        : error
          ? "work-form-error"
          : key.endsWith("_year") || key.endsWith("_month")
            ? "work-date-help"
            : undefined,
    };
  }
  return (
    <form
      ref={form}
      className="work-editor"
      onSubmit={save}
      aria-label={entry ? "Редактирование опыта" : "Добавление опыта"}
    >
      <p id="work-date-help" className="profile-hint">
        Укажите должность или компанию. Год можно указать без месяца.
      </p>
      {(["position", "company"] as const).map((key) => (
        <div key={key}>
          <label htmlFor={`work-${key}`}>{workFieldLabels[key]}</label>
          <input
            {...aria(key)}
            maxLength={200}
            value={draft[key] ?? ""}
            onChange={(e) => change(key, e.target.value || null)}
            disabled={pending || checking}
          />
          {errors[key] && <p id={`work-error-${key}`}>{errors[key]}</p>}
        </div>
      ))}
      <div>
        <label id="work-engagement-label" htmlFor="work-engagement_kind">
          Тип занятости
        </label>
        <ListboxSelect
          id="work-engagement_kind"
          labelId="work-engagement-label"
          value={draft.engagement_kind}
          options={engagementKinds}
          labels={engagementLabels}
          onChange={(value) => change("engagement_kind", value)}
          disabled={pending || checking}
          invalid={Boolean(errors.engagement_kind)}
          describedBy={
            errors.engagement_kind ? "work-error-engagement_kind" : undefined
          }
        />
        {errors.engagement_kind && (
          <p id="work-error-engagement_kind">{errors.engagement_kind}</p>
        )}
      </div>
      <div className="work-period-grid">
        {(["start", "end"] as const)
          .filter((side) => side !== "end" || draft.is_current !== true)
          .map((side) => (
            <fieldset key={side} disabled={pending || checking}>
              <legend>{side === "start" ? "Начало" : "Окончание"}</legend>
              <div className="work-date-controls">
                {(["year", "month"] as const).map((part) => {
                  const key = `${side}_${part}` as
                    "start_year" | "start_month" | "end_year" | "end_month";
                  return (
                    <div key={key}>
                      <label htmlFor={`work-${key}`}>
                        {workFieldLabels[key]}
                      </label>
                      <input
                        {...aria(key)}
                        type="number"
                        min={part === "year" ? 1900 : 1}
                        max={part === "year" ? 9999 : 12}
                        step={1}
                        inputMode="numeric"
                        value={draft[key] ?? ""}
                        onChange={(e) => {
                          const value =
                            e.target.value === ""
                              ? null
                              : Number(e.target.value);
                          setDraft((current) => ({
                            ...current,
                            [key]: value,
                            ...(side === "end" && value !== null
                              ? { is_current: false }
                              : {}),
                          }));
                        }}
                      />
                      {errors[key] && (
                        <p id={`work-error-${key}`}>{errors[key]}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </fieldset>
          ))}
      </div>
      <label className="work-current" htmlFor="work-is_current">
        <input
          {...aria("is_current")}
          type="checkbox"
          checked={draft.is_current === true}
          disabled={pending || checking}
          onChange={(e) =>
            setDraft((current) => ({
              ...current,
              is_current: e.target.checked,
              ...(e.target.checked ? { end_year: null, end_month: null } : {}),
            }))
          }
        />
        По настоящее время
      </label>
      {errors.is_current && (
        <p id="work-error-is_current">{errors.is_current}</p>
      )}
      {error && (
        <div
          id="work-form-error"
          role="alert"
          tabIndex={-1}
          className="notice error"
        >
          {error}
        </div>
      )}
      {uncertain && (
        <button
          type="button"
          onClick={() => void check()}
          disabled={pending || checking}
        >
          Проверить актуальный опыт
        </button>
      )}
      <div className="work-actions">
        <button
          type="submit"
          className="primary"
          disabled={
            !Object.keys(changes).length || pending || checking || uncertain
          }
        >
          {pending ? "Сохраняем…" : "Сохранить"}
        </button>
        <button
          type="button"
          onClick={() => {
            if (pending) {
              cancelRequested.current = true;
              setError(
                "Запрос уже отправлен. Закроем форму после получения результата.",
              );
            } else onCancel();
          }}
        >
          Отмена
        </button>
      </div>
    </form>
  );
}

export function WorkExperienceList({
  items,
  onChange,
  disabled = false,
}: {
  items: WorkExperienceEntry[];
  onChange: (
    update: (items: WorkExperienceEntry[]) => WorkExperienceEntry[],
  ) => void;
  disabled?: boolean;
}) {
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState<{ text: string } | null>(null);
  const buttons = useRef(
    new Map<number | "new" | `delete:${number}`, HTMLButtonElement>(),
  );
  const confirm = useRef<HTMLButtonElement>(null);
  const locked = useRef(false);
  useEffect(() => {
    if (deleting !== null) confirm.current?.focus();
  }, [deleting]);
  useEffect(() => {
    if (!feedback) return;
    const timer = setTimeout(() => setFeedback(null), 4000);
    return () => clearTimeout(timer);
  }, [feedback]);
  function focus(key: number | "new" | `delete:${number}`) {
    requestAnimationFrame(() => buttons.current.get(key)?.focus());
  }
  function close() {
    const key = editing;
    setEditing(null);
    if (key !== null) focus(key);
  }
  function saved(entry: WorkExperienceEntry) {
    onChange((current) =>
      editing === "new"
        ? [entry, ...current.filter((item) => item.id !== entry.id)]
        : current.map((item) => (item.id === entry.id ? entry : item)),
    );
    close();
    setFeedback({ text: "Опыт работы сохранён." });
  }
  async function remove(id: number) {
    if (locked.current) return;
    locked.current = true;
    setPending(true);
    setError("");
    try {
      await webRequest(`/api/profile/work-experiences/${id}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      onChange((current) => current.filter((item) => item.id !== id));
      setDeleting(null);
      setFeedback({ text: "Опыт работы удалён." });
      focus("new");
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      locked.current = false;
      setPending(false);
    }
  }
  return (
    <div className="work-experience-list" data-editing={editing !== null}>
      <p className="profile-hint">
        Импорт резюме заменяет этот список целиком.
      </p>
      {feedback && (
        <p className="work-feedback" role="status" aria-live="polite">
          {feedback.text}
        </p>
      )}
      {!items.length && editing !== "new" && (
        <p className="profile-empty">Опыт работы пока не добавлен.</p>
      )}
      <button
        type="button"
        className="work-add"
        ref={(node) => {
          if (node) buttons.current.set("new", node);
        }}
        disabled={disabled || editing !== null || deleting !== null}
        onClick={() => {
          setEditing("new");
          setFeedback(null);
        }}
      >
        Добавить опыт
      </button>
      {editing === "new" && <Editor onSaved={saved} onCancel={close} />}
      <ul className="profile-records work-records">
        {items.map((entry) => (
          <li key={entry.id}>
            {editing === entry.id ? (
              <Editor entry={entry} onSaved={saved} onCancel={close} />
            ) : (
              <>
                <strong>{entry.position || entry.company}</strong>
                {entry.position && entry.company && (
                  <span>{entry.company}</span>
                )}
                <small>
                  {period(entry.start_year, entry.start_month)} —{" "}
                  {entry.is_current
                    ? "Сейчас"
                    : period(entry.end_year, entry.end_month)}{" "}
                  · {engagementLabels[entry.engagement_kind]}
                </small>
                {entry.duration_months !== null && (
                  <small>{entry.duration_months} мес.</small>
                )}
                {deleting === entry.id ? (
                  <div className="work-confirm">
                    <p>Удалить этот опыт работы?</p>
                    {error && <p role="alert">{error}</p>}
                    <div className="work-actions work-confirm-actions">
                      <button
                        type="button"
                        ref={confirm}
                        className="work-text-action work-delete"
                        disabled={pending}
                        onClick={() => void remove(entry.id)}
                      >
                        Удалить
                      </button>
                      <button
                        type="button"
                        className="text-action"
                        disabled={pending}
                        onClick={() => {
                          setDeleting(null);
                          focus(`delete:${entry.id}`);
                        }}
                      >
                        Отмена
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="work-actions work-read-actions">
                    <button
                      type="button"
                      aria-label="Редактировать опыт"
                      className="text-action"
                      ref={(node) => {
                        if (node) buttons.current.set(entry.id, node);
                      }}
                      disabled={
                        disabled || editing !== null || deleting !== null
                      }
                      onClick={() => {
                        setEditing(entry.id);
                        setFeedback(null);
                      }}
                    >
                      Редактировать
                    </button>
                    <button
                      type="button"
                      className="work-text-action work-delete"
                      ref={(node) => {
                        if (node)
                          buttons.current.set(`delete:${entry.id}`, node);
                      }}
                      disabled={
                        disabled || editing !== null || deleting !== null
                      }
                      onClick={() => {
                        setDeleting(entry.id);
                        setError("");
                        setFeedback(null);
                      }}
                    >
                      Удалить
                    </button>
                  </div>
                )}
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
