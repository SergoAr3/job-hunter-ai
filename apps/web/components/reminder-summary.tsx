"use client";
import { useBrowserTimezone, useCurrentTime } from "../lib/browser-timezone";
import type { ReminderFields } from "../lib/contracts";

export function ReminderSummary({
  value,
  legacy,
  temporal = true,
  browserTimezone = true,
  telegramLinked,
}: {
  value: ReminderFields;
  legacy?: string | null;
  temporal?: boolean;
  browserTimezone?: boolean;
  telegramLinked?: boolean;
}) {
  const detected = useBrowserTimezone();
  const now = useCurrentTime();
  const zone =
    (browserTimezone && detected) || value.next_action_timezone || "";
  const exact = value.next_action_remind_at;
  if (!exact)
    return legacy ? (
      <span className="muted">
        Дата без уведомления: <time dateTime={legacy}>{legacy}</time>
      </span>
    ) : null;
  let display = exact;
  try {
    if (zone)
      display = new Intl.DateTimeFormat("ru-RU", {
        timeZone: zone,
        day: "numeric",
        month: "long",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(exact));
  } catch {}
  return (
    <span className="reminder-summary">
      <time dateTime={exact}>{display}</time>
      {zone && <span className="muted"> · {zone}</span>}
      {temporal && Date.parse(exact) <= now && (
        <span className="reminder-attention">Время напоминания прошло</span>
      )}
      <ReminderDeliveryStatus value={value} telegramLinked={telegramLinked} />
    </span>
  );
}

export function ReminderDeliveryStatus({
  value,
  telegramLinked,
  editing = false,
}: {
  value: ReminderFields;
  telegramLinked?: boolean;
  editing?: boolean;
}) {
  const failed = value.reminder_delivery_state === "failed";
  const uncertain = value.reminder_failure_reason === "uncertain";
  return (
    <>
      <span
        className={`reminder-delivery ${failed ? "status-error" : "muted"}`}
      >
        {failed
          ? uncertain
            ? "Не удалось подтвердить отправку. Напоминание могло прийти."
            : value.reminder_failure_reason === "telegram_not_connected"
              ? editing
                ? "Напоминание не отправлено: Telegram не подключён."
                : "Подключите Telegram, чтобы получать уведомления."
              : "Не удалось отправить напоминание"
          : value.reminder_delivery_state === "sent"
            ? "Напоминание отправлено"
            : telegramLinked === false
              ? "Запланировано только в Job Hunter AI"
              : "Уведомление запланировано"}
      </span>
      {uncertain && (
        <span className="muted">Автоматически повторять не будем.</span>
      )}
    </>
  );
}
