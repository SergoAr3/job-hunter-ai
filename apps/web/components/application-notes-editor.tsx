"use client";
import { useEffect, useRef, useState } from "react";
import type { ApplicationDetail } from "../lib/contracts";
import { webRequest } from "../lib/client";
import { errorMessage, WebError } from "../lib/errors";

type Application = ApplicationDetail["application"];
type Field = "note" | "next_action";
type Saved = (application: Application, field: Field) => void;

export function ApplicationNotesEditor({
  application,
  onSaved,
}: {
  application: Application;
  onSaved: Saved;
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
}: {
  field: Field;
  application: Application;
  onSaved: Saved;
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
  const [feedback, setFeedback] = useState("");
  const [previousApplication, setPreviousApplication] = useState(application);
  const locked = useRef(false);
  const mounted = useRef(true);
  const header = useRef<HTMLButtonElement>(null);
  const dirty = value !== (confirmed[field] ?? "");

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
      previousApplication.next_action_due_on !== application.next_action_due_on)
  ) {
    setPreviousApplication(application);
    if (!dirty && !busy && !uncertain) {
      setConfirmed(application);
      setValue(application[field] ?? "");
    }
  }

  function accept(fresh: Application) {
    setConfirmed(fresh);
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

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked.current || uncertain || !dirty) return;
    if ([...value.trim()].length > limit || value.includes("\0")) {
      setError(`${title}: до ${limit} символов. Удалите недопустимые символы.`);
      setOpen(true);
      return;
    }
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
          body: JSON.stringify({ [field]: value.trim() || null }),
        },
      );
      validateResponse(fresh);
      if (!mounted.current) return;
      accept(fresh.application);
      setFeedback(
        field === "note"
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
      setError(errorMessage(failure));
      setOpen(true);
    } finally {
      if (mounted.current) setBusy(false);
      locked.current = false;
    }
  }

  async function checkSaved() {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    try {
      const fresh = await webRequest<ApplicationDetail>(
        `/api/applications/${application.id}`,
      );
      validateResponse(fresh);
      if (!mounted.current) return;
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
            aria-describedby={`${id}-hint${error ? ` ${id}-error` : ""}`}
            aria-invalid={!!error}
            onChange={(event) => {
              setValue(event.target.value);
              setError("");
              setFeedback("");
            }}
          />
          <p id={`${id}-hint`} className="muted">
            До {limit} символов.
          </p>
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
                setError("");
                setFeedback("");
                setOpen(false);
                header.current?.focus();
              }}
            >
              Отмена
            </button>
          </div>
          {error && (
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
          Существующая дата:{" "}
          <time dateTime={confirmed.next_action_due_on}>
            {confirmed.next_action_due_on}
          </time>
          .
        </p>
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
