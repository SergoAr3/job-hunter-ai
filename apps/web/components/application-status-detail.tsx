"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import type {
  ApplicationDetail,
  ApplicationStatusHistory,
} from "../lib/contracts";
import type { ApplicationStatus } from "../lib/applications";
import { webRequest } from "../lib/client";
import { errorMessage, WebError } from "../lib/errors";
import { ApplicationView } from "./application-detail";
import { ApplicationHistory } from "./application-history";
import { ApplicationNotesEditor } from "./application-notes-editor";
import { StatusSelect } from "./status-select";

export function ApplicationStatusDetail({
  detail: initial,
  telegramLinked,
}: {
  detail: ApplicationDetail;
  telegramLinked?: boolean;
}) {
  const [detail, setDetail] = useState(initial);
  const [selected, setSelected] = useState(
    initial.application.status as ApplicationStatus,
  );
  const [pending, setPending] = useState<"idle" | "saving" | "checking">(
    "idle",
  );
  const [uncertain, setUncertain] = useState(false);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<ApplicationStatusHistory | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState(false);
  const locked = useRef(false);
  const mounted = useRef(true);
  const historyRequested = useRef(false);
  const historyLoaded = useRef(false);
  const historyRequest = useRef<Promise<void> | null>(null);
  const id = String(initial.application.id);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  function loadHistory(afterConfirmedMutation = false) {
    if (!mounted.current) return Promise.resolve();
    historyRequested.current = true;
    const previous = historyRequest.current;
    if (previous && !afterConfirmedMutation) return previous;
    const current = (async () => {
      if (previous) await previous;
      if (!mounted.current) return;
      setHistoryLoading(true);
      setHistoryError(false);
      try {
        const fresh = await webRequest<ApplicationStatusHistory>(
          `/api/applications/${id}/status-history`,
        );
        if (!Array.isArray(fresh?.items)) throw new WebError("api_unavailable");
        if (!mounted.current) return;
        setHistory(fresh);
        historyLoaded.current = true;
      } catch (failure) {
        if (!mounted.current) return;
        if (
          failure instanceof WebError &&
          failure.code === "APPLICATION_NOT_FOUND" &&
          !afterConfirmedMutation
        )
          setMissing(true);
        else setHistoryError(true);
      } finally {
        if (mounted.current) setHistoryLoading(false);
      }
    })();
    historyRequest.current = current;
    const clearRequest = () => {
      if (historyRequest.current === current) historyRequest.current = null;
    };
    void current.then(clearRequest, clearRequest);
    return current;
  }

  function toggleHistory() {
    if (historyOpen) {
      setHistoryOpen(false);
      return;
    }
    setHistoryOpen(true);
    if (!historyLoaded.current) void loadHistory();
  }

  async function readCurrent(
    afterMutation: boolean,
    intended?: ApplicationStatus,
  ) {
    setPending("checking");
    try {
      const fresh = await webRequest<ApplicationDetail>(
        `/api/applications/${id}`,
      );
      if (
        fresh?.application?.id !== initial.application.id ||
        typeof fresh.application.status !== "string"
      )
        throw new WebError("api_unavailable");
      if (!mounted.current) return false;
      // This read confirms status, not CRM fields saved while it was in flight.
      setDetail((current) => ({
        ...current,
        application: {
          ...current.application,
          status: fresh.application.status,
          next_action_suggestions: fresh.application.next_action_suggestions,
        },
      }));
      setSelected(fresh.application.status as ApplicationStatus);
      setUncertain(false);
      setError("");
      setFeedback(
        afterMutation && fresh.application.status === intended
          ? "Статус сохранён и подтверждён."
          : "Показан актуальный статус из API.",
      );
      return true;
    } catch (failure) {
      if (!mounted.current) return false;
      if (
        failure instanceof WebError &&
        failure.code === "APPLICATION_NOT_FOUND"
      ) {
        setMissing(true);
      } else {
        setUncertain(true);
        setError(
          afterMutation
            ? errorMessage(new WebError("status_unconfirmed"))
            : "Не удалось загрузить актуальный статус. Повторите чтение.",
        );
      }
      return false;
    } finally {
      if (mounted.current) setPending("idle");
    }
  }

  async function submitStatus(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked.current || uncertain || selected === detail.application.status)
      return;
    locked.current = true;
    setPending("saving");
    setError("");
    setFeedback("");
    try {
      await webRequest<{ ok: boolean }>(`/api/applications/${id}/status`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: selected }),
      });
      if (!mounted.current) return;
      const confirmed = await readCurrent(true, selected);
      if (confirmed && historyRequested.current) void loadHistory(true);
    } catch (failure) {
      if (!mounted.current) return;
      if (
        failure instanceof WebError &&
        failure.code === "APPLICATION_NOT_FOUND"
      ) {
        setMissing(true);
      } else if (
        failure instanceof WebError &&
        failure.code === "ambiguous_status"
      ) {
        setUncertain(true);
        setError(errorMessage(failure));
      } else {
        setError(errorMessage(failure));
      }
    } finally {
      if (mounted.current) setPending("idle");
      locked.current = false;
    }
  }

  async function refreshStatus() {
    if (locked.current) return;
    locked.current = true;
    setError("");
    setFeedback("");
    try {
      await readCurrent(false);
    } finally {
      locked.current = false;
    }
  }

  if (missing)
    return (
      <div className="state">
        <h1>Вакансия не найдена</h1>
        <p>Эта запись недоступна у настроенного пользователя.</p>
        <Link href="/applications">К моим вакансиям</Link>
      </div>
    );

  const busy = pending !== "idle";
  return (
    <ApplicationView
      detail={detail}
      statusUnconfirmed={uncertain}
      notesControl={
        <ApplicationNotesEditor
          application={detail.application}
          telegramLinked={telegramLinked}
          onSaved={(application, field) =>
            setDetail((current) => ({
              ...current,
              application: {
                ...current.application,
                [field]: application[field],
                ...(field === "next_action"
                  ? {
                      next_action_due_on: application.next_action_due_on,
                      next_action_remind_at: application.next_action_remind_at,
                      next_action_timezone: application.next_action_timezone,
                      reminder_delivery_state:
                        application.reminder_delivery_state,
                      reminder_sent_at: application.reminder_sent_at,
                      reminder_failure_reason:
                        application.reminder_failure_reason,
                    }
                  : {}),
              },
            }))
          }
        />
      }
      historySection={
        <ApplicationHistory
          open={historyOpen}
          loading={historyLoading}
          history={history}
          error={historyError}
          onToggle={toggleHistory}
          onRetry={() => void loadHistory()}
        />
      }
      statusControl={
        <div className="application-status-control">
          <form onSubmit={(event) => void submitStatus(event)}>
            <label id="application-status-label" htmlFor="application-status">
              Статус
            </label>
            <div className="application-status-fields">
              <StatusSelect
                value={selected}
                disabled={busy || uncertain}
                labelId="application-status-label"
                onChange={(status) => {
                  setSelected(status);
                  setError("");
                  setFeedback("");
                }}
              />
              <button
                type="submit"
                className="status-save-button"
                disabled={
                  busy || uncertain || selected === detail.application.status
                }
              >
                Сохранить статус
              </button>
            </div>
          </form>
          {busy && (
            <p className="status-feedback" role="status">
              {pending === "saving"
                ? "Сохраняем статус…"
                : "Проверяем актуальный статус…"}
            </p>
          )}
          {feedback && (
            <p
              className="status-feedback status-feedback-success"
              role="status"
            >
              {feedback}
            </p>
          )}
          {error && (
            <div className="status-error" role="alert">
              <p>{error}</p>
              {uncertain && (
                <button
                  type="button"
                  onClick={() => void refreshStatus()}
                  disabled={busy}
                >
                  Обновить данные
                </button>
              )}
            </div>
          )}
        </div>
      }
    />
  );
}
