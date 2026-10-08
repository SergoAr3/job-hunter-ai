import { WebError } from "./errors";

export function localParts(instant: string, zone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(instant));
  const v = Object.fromEntries(parts.map((p) => [p.type, p.value]));
  return {
    date: `${v.year}-${v.month}-${v.day}`,
    time: `${v.hour}:${v.minute}`,
    seconds: v.second,
  };
}

// Encoding the user's wall time, not choosing a business schedule. API validates again.
export function reminderInput(date: string, time: string, zone: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time) || !zone)
    throw new WebError("REMINDER_DATETIME_INVALID");
  const wall = Date.parse(`${date}T${time}:00Z`);
  if (!Number.isFinite(wall)) throw new WebError("REMINDER_DATETIME_INVALID");
  const candidates = new Map<number, number>();
  try {
    for (const hours of [-48, -24, 0, 24, 48]) {
      const sample = wall + hours * 3600000;
      const parts = localParts(new Date(sample).toISOString(), zone);
      const offset =
        Date.parse(`${parts.date}T${parts.time}:${parts.seconds}Z`) - sample;
      const candidate = wall - offset;
      const check = localParts(new Date(candidate).toISOString(), zone);
      if (check.date === date && check.time === time && check.seconds === "00")
        candidates.set(candidate, offset);
    }
  } catch {
    throw new WebError("REMINDER_TIMEZONE_INVALID");
  }
  if (!candidates.size) throw new WebError("REMINDER_TIME_NONEXISTENT");
  if (candidates.size > 1) throw new WebError("REMINDER_TIME_AMBIGUOUS");
  const offset = [...candidates.values()][0] / 60000;
  const abs = Math.abs(offset);
  return `${date}T${time}:00${offset < 0 ? "-" : "+"}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
}
