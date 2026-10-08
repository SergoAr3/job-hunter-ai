"use client";
import { useEffect, useRef, useState } from "react";
import type { ApplicationDetail } from "../lib/contracts";
import { webRequest } from "../lib/client";
import { localParts, reminderInput } from "../lib/reminder-time";
import { ReminderSummary, ReminderDeliveryStatus } from "./reminder-summary";
import {
  useBrowserTimezone,
  useAvailableTimezones,
} from "../lib/browser-timezone";
import { ListboxSelect } from "./listbox-select";
import { errorMessage, WebError } from "../lib/errors";

type Application = ApplicationDetail["application"];
type Field = "note" | "next_action";
type ErrorOwner = "action" | "date" | "time" | "reminder" | "zone" | "form";
type Saved = (application: Application, field: Field) => void;

export function ApplicationNotesEditor({
  application,
  onSaved,
  telegramLinked,
}: {
  application: Application;
  onSaved: Saved;
  telegramLinked?: boolean;
}) {
  return (
    <div className="crm-notes application-notes-editor">
      <ApplicationFieldEditor
        field="note"
        application={application}
        onSaved={onSaved}
      />
      <ApplicationFieldEditor
        field="next_action"
        telegramLinked={telegramLinked}
        application={application}
        onSaved={onSaved}
      />
    </div>
  );
}

function ApplicationFieldEditor({
  field,
  application,
  onSaved,
  telegramLinked,
}: {
  field: Field;
  application: Application;
  onSaved: Saved;
  telegramLinked?: boolean;
}) {
  const title = field === "note" ? "Заметки" : "Следующее действие";
  const limit = field === "note" ? 1000 : 500;
  const id = `application-${field}`;
  const [open, setOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(application);
  const [value, setValue] = useState(application[field] ?? "");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [errorOwner, setErrorOwner] = useState<ErrorOwner>("form");
  const actionInvalid = !!error && errorOwner === "action";
  const reminderError =
    !!error && ["date", "time", "reminder", "zone"].includes(errorOwner);
  const [feedback, setFeedback] = useState("");
  const [previousApplication, setPreviousApplication] = useState(application);
  const locked = useRef(false);
  const mounted = useRef(true);
  const header = useRef<HTMLButtonElement>(null);
  const dateInput = useRef<HTMLInputElement>(null);
  const focusDateAfterReveal = useRef(false);
  const initialZone = application.next_action_timezone || "";
  const initialParts =
    application.next_action_remind_at && initialZone
      ? localParts(application.next_action_remind_at, initialZone)
      : { date: "", time: "" };
  const [date, setDate] = useState(initialParts.date);
  const [time, setTime] = useState(initialParts.time);
  const [reminderRevealed, setReminderRevealed] = useState(
    !!application.next_action_remind_at,
  );
  const reminderVisible = reminderRevealed || !!confirmed.next_action_remind_at;
  useEffect(() => {
    if (reminderVisible && focusDateAfterReveal.current) {
      focusDateAfterReveal.current = false;
      dateInput.current?.focus();
    }
  }, [reminderVisible]);
  const detectedZone = useBrowserTimezone();
  const [selectedZone, setZone] = useState(initialZone);
  const zone = selectedZone || detectedZone;
  const zones = useAvailableTimezones();
  const intended = useRef<Record<string, string | null> | null>(null);
  const savedParts =
    confirmed.next_action_remind_at && confirmed.next_action_timezone
      ? localParts(
          confirmed.next_action_remind_at,
          confirmed.next_action_timezone,
        )
      : { date: "", time: "" };
  const reminderDirty =
    field === "next_action" &&
    (date !== savedParts.date ||
      time !== savedParts.time ||
      (!!(date || time) &&
        !!confirmed.next_action_timezone &&
        zone !== confirmed.next_action_timezone));
  const dirty = value !== (confirmed[field] ?? "") || reminderDirty;

  function resetReminder(fresh: Application) {
    const name = fresh.next_action_timezone || zone;
    const parts =
      fresh.next_action_remind_at && name
        ? localParts(fresh.next_action_remind_at, name)
        : { date: "", time: "" };
    setReminderRevealed(!!fresh.next_action_remind_at);
    setDate(parts.date);
    setTime(parts.time);
    setZone(name);
  }

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!feedback) return;
    const timer = setTimeout(() => setFeedback(""), 4000);
    return () => clearTimeout(timer);
  }, [feedback]);

  if (
    previousApplication[field] !== application[field] ||
    (field === "next_action" &&
      (previousApplication.next_action_due_on !==
        application.next_action_due_on ||
        previousApplication.next_action_remind_at !==
          application.next_action_remind_at ||
        previousApplication.reminder_delivery_state !==
          application.reminder_delivery_state ||
        previousApplication.next_action_timezone !==
          application.next_action_timezone))
  ) {
    setPreviousApplication(application);
    if (!dirty && !busy && !uncertain) {
      setConfirmed(application);
      setValue(application[field] ?? "");
      if (field === "next_action") resetReminder(application);
    }
  }

  function accept(fresh: Application) {
    setConfirmed(fresh);
    if (field === "next_action") resetReminder(fresh);
    setValue(fresh[field] ?? "");
    setUncertain(false);
    setOpen(false);
    onSaved(fresh, field);
    header.current?.focus();
  }

  function validateResponse(fresh: ApplicationDetail) {
    if (
      fresh?.application?.id !== application.id ||
      ![
        fresh.application.note,
        fresh.application.next_action,
        fresh.application.next_action_due_on,
      ].every((text) => text === null || typeof text === "string")
    )
      throw new WebError("ambiguous_application");
  }

  function fail(message: string, owner: ErrorOwner) {
    setError(message);
    setErrorOwner(owner);
    setOpen(true);
  }

  function failureOwner(failure: unknown): ErrorOwner {
    if (failure instanceof WebError) {
      if (
        failure.code === "REMINDER_ACTION_REQUIRED" ||
        failure.code === "application_invalid"
      )
        return "action";
      if (failure.code === "REMINDER_TIMEZONE_INVALID") return "zone";
      if (failure.code.startsWith("REMINDER_")) return "reminder";
    }
    return "form";
  }

  async function save(event?: React.FormEvent<HTMLFormElement>, done = false) {
    event?.preventDefault();
    if (locked.current || uncertain || (!done && !dirty) || (done && dirty))
      return;
    if ([...value.trim()].length > limit || value.includes("\0")) {
      fail(
        `${title}: до ${limit} символов. Удалите недопустимые символы.`,
        "action",
      );
      return;
    }
    if (field === "next_action" && !done && (date || time)) {
      if (!value.trim()) {
        fail(errorMessage(new WebError("REMINDER_ACTION_REQUIRED")), "action");
        return;
      }
      if (!date) {
        fail("Укажите дату напоминания.", "date");
        return;
      }
      if (!time) {
        fail("Укажите время напоминания.", "time");
        return;
      }
      if (!zone) {
        fail(errorMessage(new WebError("REMINDER_TIMEZONE_INVALID")), "zone");
        return;
      }
    }
    const changes: Record<string, string | null> = {
      [field]: done ? null : value.trim() || null,
    };
    if (
      field === "next_action" &&
      changes.next_action &&
      !done &&
      reminderDirty
    ) {
      try {
        changes.next_action_remind_at =
          date || time ? reminderInput(date, time, zone) : null;
        if (changes.next_action_remind_at) changes.next_action_timezone = zone;
      } catch (failure) {
        fail(errorMessage(failure), failureOwner(failure));
        return;
      }
    }
    intended.current = changes;
    locked.current = true;
    setBusy(true);
    setError("");
    setFeedback("");
    try {
      const fresh = await webRequest<ApplicationDetail>(
        `/api/applications/${application.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(changes),
        },
      );
      validateResponse(fresh);
      if (!mounted.current) return;
      accept(fresh.application);
      setFeedback(
        done
          ? "Действие выполнено."
          : field === "note"
            ? "Заметки сохранены."
            : "Следующее действие сохранено.",
      );
    } catch (failure) {
      if (!mounted.current) return;
      if (
        failure instanceof WebError &&
        failure.code === "ambiguous_application"
      )
        setUncertain(true);
      fail(errorMessage(failure), failureOwner(failure));
    } finally {
      if (mounted.current) setBusy(false);
      locked.current = false;
    }
  }

  async function checkSaved() {
    if (locked.current) return;
    setErrorOwner("form");
    locked.current = true;
    setBusy(true);
    try {
      const fresh = await webRequest<ApplicationDetail>(
        `/api/applications/${application.id}`,
      );
      validateResponse(fresh);
      if (!mounted.current) return;
      const matches = Object.entries(intended.current || {}).every(
        ([key, value]) => {
          const saved = fresh.application[key as keyof Application];
          return key === "next_action_remind_at" &&
            value !== null &&
            typeof saved === "string"
            ? Date.parse(value) === Date.parse(saved)
            : saved === value;
        },
      );
      if (!matches) {
        setConfirmed(fresh.application);
        setUncertain(false);
        onSaved(fresh.application, field);
        setError(
          "Сохранённое значение отличается от отправленного. Проверьте черновик и сохраните снова.",
        );
        return;
      }
      accept(fresh.application);
      setError("");
      setFeedback("Показано актуальное значение из API.");
    } catch (failure) {
      if (mounted.current) {
        setError(errorMessage(failure));
        setOpen(true);
      }
    } finally {
      if (mounted.current) setBusy(false);
      locked.current = false;
    }
  }

  return (
    <section
      className="application-note-section"
      aria-labelledby={`${id}-header`}
    >
      <h2>
        <button
          ref={header}
          id={`${id}-header`}
          type="button"
          className="application-note-toggle"
          aria-expanded={open}
          aria-controls={`${id}-panel`}
          aria-disabled={busy || !!error}
          onClick={() => {
            if (!busy && !error) {
              setOpen(!open);
              setFeedback("");
            }
          }}
        >
          <span>{title}</span>
          <svg
            className="application-note-chevron"
            aria-hidden="true"
            focusable="false"
            viewBox="0 0 16 16"
            width="16"
            height="16"
          >
            <path
              d="m4 6 4 4 4-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            />
          </svg>
        </button>
      </h2>
      {!open && (
        <p
          className={`application-note-summary${field === "note" ? " application-note-excerpt" : ""}`}
        >
          {confirmed[field] || (field === "note" ? "Нет заметок" : "Не задано")}
        </p>
      )}
      <div id={`${id}-panel`} hidden={!open}>
        <form onSubmit={(event) => void save(event)} aria-busy={busy}>
          <textarea
            id={id}
            aria-labelledby={`${id}-header`}
            rows={field === "note" ? 4 : 1}
            value={value}
            disabled={busy || uncertain}
            placeholder={
              field === "next_action" ? "Написать рекрутеру" : undefined
            }
            aria-describedby={`${id}-hint${actionInvalid ? ` ${id}-error` : ""}`}
            aria-invalid={actionInvalid}
            onChange={(event) => {
              setValue(event.target.value);
              setError("");
              setFeedback("");
            }}
          />
          <p id={`${id}-hint`} className="muted">
            {field === "note"
              ? "Детали разговоров, условия, вопросы, ссылки и договорённости по вакансии."
              : "Что сделать дальше по этой вакансии. Напоминание можно добавить отдельно."}{" "}
            До {limit} символов.
          </p>
          {actionInvalid && (
            <p id={`${id}-error`} className="status-error" role="alert">
              {error}
            </p>
          )}
          {field === "next_action" && (
            <>
              {!!application.next_action_suggestions?.length && (
                <div
                  className="action-suggestions"
                  aria-label="Примеры следующих действий"
                >
                  {application.next_action_suggestions.map((suggestion) => (
                    <button
                      key={suggestion.id}
                      type="button"
                      disabled={busy || uncertain}
                      onClick={() => {
                        setValue(suggestion.action_text);
                        setError("");
                        setFeedback("");
                      }}
                    >
                      {suggestion.label}
                    </button>
                  ))}
                </div>
              )}
              {!reminderVisible && (
                <button
                  type="button"
                  className="text-action"
                  aria-expanded={false}
                  aria-controls={`${id}-reminder`}
                  disabled={busy || uncertain}
                  onClick={() => {
                    focusDateAfterReveal.current = true;
                    setReminderRevealed(true);
                  }}
                >
                  + Добавить напоминание
                </button>
              )}
              <div id={`${id}-reminder`} hidden={!reminderVisible}>
                {reminderVisible && (
                  <>
                    <div className="reminder-inputs">
                      <div>
                        <label htmlFor={`${id}-date`}>Дата напоминания</label>
                        <input
                          ref={dateInput}
                          type="date"
                          id={`${id}-date`}
                          value={date}
                          disabled={busy || uncertain}
                          aria-describedby={
                            error &&
                            (errorOwner === "date" || errorOwner === "reminder")
                              ? `${id}-reminder-error`
                              : undefined
                          }
                          aria-invalid={
                            !!error &&
                            (errorOwner === "date" || errorOwner === "reminder")
                          }
                          onChange={(e) => {
                            setDate(e.target.value);
                            setError("");
                          }}
                        />
                      </div>
                      <div>
                        <label htmlFor={`${id}-time`}>Время напоминания</label>
                        <input
                          type="time"
                          step="60"
                          id={`${id}-time`}
                          value={time}
                          disabled={busy || uncertain}
                          aria-describedby={
                            error &&
                            (errorOwner === "time" || errorOwner === "reminder")
                              ? `${id}-reminder-error`
                              : undefined
                          }
                          aria-invalid={
                            !!error &&
                            (errorOwner === "time" || errorOwner === "reminder")
                          }
                          onChange={(e) => {
                            setTime(e.target.value);
                            setError("");
                          }}
                        />
                      </div>
                    </div>
                    {reminderError && (
                      <p
                        id={`${id}-reminder-error`}
                        className="status-error"
                        role="alert"
                      >
                        {error}
                      </p>
                    )}
                    {zone && !(error && errorOwner === "zone") ? (
                      <p className="muted">Часовой пояс: {zone}</p>
                    ) : (
                      <div>
                        <span id={`${id}-zone-label`}>
                          Выберите часовой пояс
                        </span>
                        <ListboxSelect
                          invalid={!!error && errorOwner === "zone"}
                          describedBy={
                            error && errorOwner === "zone"
                              ? `${id}-reminder-error`
                              : undefined
                          }
                          id={`${id}-zone`}
                          labelId={`${id}-zone-label`}
                          value={zone}
                          options={zones}
                          labels={Object.fromEntries(
                            zones.map((name) => [name, name]),
                          )}
                          disabled={busy || uncertain}
                          onChange={(name) => {
                            setZone(name);
                            setError("");
                          }}
                        />
                      </div>
                    )}
                    {confirmed.next_action_due_on && (date || time) && (
                      <p className="muted">
                        Точное напоминание заменит существующую дату.
                      </p>
                    )}
                    {telegramLinked !== undefined && (
                      <p className="muted">
                        {telegramLinked ? (
                          "Уведомление придёт в Telegram."
                        ) : (
                          <>
                            Без Telegram это напоминание будет видно только в
                            Job Hunter AI.
                            <br />
                            Подключите Telegram, чтобы получать уведомления.
                          </>
                        )}
                      </p>
                    )}
                    {confirmed.next_action_remind_at &&
                      ["sent", "failed"].includes(
                        confirmed.reminder_delivery_state || "",
                      ) && <ReminderDeliveryStatus value={confirmed} editing />}
                    {confirmed.next_action_remind_at && (
                      <button
                        className="text-action"
                        type="button"
                        disabled={busy || uncertain}
                        onClick={() => {
                          setDate("");
                          setTime("");
                          setError("");
                        }}
                      >
                        Убрать напоминание
                      </button>
                    )}
                  </>
                )}
              </div>
            </>
          )}
          <div className="application-notes-actions">
            <button
              type="submit"
              className="primary"
              disabled={busy || uncertain || !dirty}
            >
              Сохранить
            </button>
            <button
              type="button"
              disabled={busy || uncertain}
              onClick={() => {
                setValue(confirmed[field] ?? "");
                if (field === "next_action") resetReminder(confirmed);
                setError("");
                setFeedback("");
                setOpen(false);
                header.current?.focus();
              }}
            >
              Отмена
            </button>
            {field === "next_action" && confirmed.next_action && (
              <button
                type="button"
                disabled={busy || uncertain || dirty}
                onClick={() => void save(undefined, true)}
              >
                Выполнено
              </button>
            )}
          </div>
          {error && errorOwner === "form" && (
            <div id={`${id}-error`} className="status-error" role="alert">
              <p>{error}</p>
              {uncertain && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void checkSaved()}
                >
                  Проверить сохранение
                </button>
              )}
            </div>
          )}
        </form>
      </div>
      {field === "next_action" && confirmed.next_action_due_on && (
        <p className="muted">
          Дата без уведомления. Существующая дата:{" "}
          <time dateTime={confirmed.next_action_due_on}>
            {confirmed.next_action_due_on}
          </time>
          .
        </p>
      )}
      {field === "next_action" && !open && confirmed.next_action_remind_at && (
        <ReminderSummary
          value={confirmed}
          browserTimezone={false}
          telegramLinked={telegramLinked}
        />
      )}
      {(busy || feedback || dirty) && (
        <p
          className="application-note-feedback muted"
          role="status"
          aria-live="polite"
        >
          {busy
            ? "Сохраняем или проверяем…"
            : feedback || (dirty ? "Есть несохранённые изменения." : "")}
        </p>
      )}
    </section>
  );
}
