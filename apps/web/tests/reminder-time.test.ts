import { expect, it } from "vitest";
import { localParts, reminderInput } from "../lib/reminder-time";

it("encodes wall time with an explicit validated zone offset", () => {
  expect(reminderInput("2026-10-10", "11:02", "Asia/Yerevan")).toBe(
    "2026-10-10T11:02:00+04:00",
  );
  expect(reminderInput("2026-07-10", "11:02", "America/New_York")).toBe(
    "2026-07-10T11:02:00-04:00",
  );
  expect(localParts("2026-10-10T07:02:00Z", "Asia/Yerevan")).toEqual({
    date: "2026-10-10",
    time: "11:02",
    seconds: "00",
  });
});
it.each([
  ["2027-03-14", "02:30", "America/New_York", "REMINDER_TIME_NONEXISTENT"],
  ["2026-11-01", "01:30", "America/New_York", "REMINDER_TIME_AMBIGUOUS"],
  ["2026-10-10", "11:00", "", "REMINDER_DATETIME_INVALID"],
  ["2026-10-10", "11:00", "Bad/Zone", "REMINDER_TIMEZONE_INVALID"],
])("rejects unsafe wall time %s %s", (date, time, zone, code) => {
  expect(() => reminderInput(date, time, zone)).toThrow(code);
});
