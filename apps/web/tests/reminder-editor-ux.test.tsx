import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { ApplicationNotesEditor } from "../components/application-notes-editor";
import { ReminderSummary } from "../components/reminder-summary";
import { saved } from "./fixtures";

const clock = vi.hoisted(() => ({ zone: "Asia/Yerevan" }));
vi.mock("../lib/browser-timezone", () => ({
  useBrowserTimezone: () => clock.zone,
  useAvailableTimezones: () => ["Asia/Yerevan", "UTC"],
  useCurrentTime: () => Date.parse("2026-10-08T00:00:00Z"),
}));
const schedule = {
  next_action_remind_at: "2026-10-13T08:00:00Z",
  next_action_timezone: "Asia/Yerevan",
  reminder_delivery_state: "pending" as const,
  reminder_sent_at: null,
  reminder_failure_reason: null,
};
function setup(exact = false, linked = false, reveal = true) {
  const application = {
    ...saved.application,
    next_action: "Подготовить отклик",
    next_action_due_on: null,
    next_action_remind_at: null,
    next_action_timezone: null,
    ...(exact ? schedule : {}),
    next_action_suggestions: [
      {
        id: "follow_up",
        label: "Написать рекрутеру",
        action_text: "Написать рекрутеру",
      },
    ],
  };
  render(
    <ApplicationNotesEditor
      application={application}
      onSaved={vi.fn()}
      telegramLinked={linked}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Следующее действие" }));
  if (!exact && reveal)
    fireEvent.click(
      screen.getByRole("button", { name: "+ Добавить напоминание" }),
    );
  return application;
}
const action = () =>
  screen.getByRole("textbox", { name: "Следующее действие" });
const date = () => screen.getByLabelText("Дата напоминания");
const time = () => screen.getByLabelText("Время напоминания");
const group = () =>
  within(screen.getByRole("region", { name: "Следующее действие" }));
const change = (input: HTMLElement, value: string) =>
  fireEvent.change(input, { target: { value } });
const save = async () => {
  await act(async () =>
    fireEvent.click(group().getByRole("button", { name: "Сохранить" })),
  );
};
const remove = () =>
  screen.queryByRole("button", { name: "Убрать напоминание" });
beforeEach(() => {
  clock.zone = "Asia/Yerevan";
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("saves action alone without any reminder fields and closes editor", async () => {
  const application = setup();
  change(action(), "Подготовить новый отклик");
  vi.mocked(fetch).mockResolvedValue(
    Response.json({
      ...saved,
      application: { ...application, next_action: "Подготовить новый отклик" },
    }),
  );
  await save();
  expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string)).toEqual(
    { next_action: "Подготовить новый отклик" },
  );
  expect(
    screen.getByRole("button", { name: "Следующее действие" }),
  ).toHaveAttribute("aria-expanded", "false");
  expect(remove()).not.toBeInTheDocument();
});
it.each(["date", "time"])(
  "%s alone marks only the missing control and associates the group error",
  async (part) => {
    setup();
    change(
      part === "date" ? date() : time(),
      part === "date" ? "2026-10-13" : "12:00",
    );
    expect(group().getByRole("button", { name: "Сохранить" })).toBeEnabled();
    expect(remove()).not.toBeInTheDocument();
    await save();
    const invalid = part === "date" ? time() : date();
    const valid = part === "date" ? date() : time();
    expect(invalid).toHaveAttribute("aria-invalid", "true");
    expect(invalid).toHaveAttribute(
      "aria-describedby",
      "application-next_action-reminder-error",
    );
    expect(valid).toHaveAttribute("aria-invalid", "false");
    expect(action()).toHaveAttribute("aria-invalid", "false");
    expect(action()).not.toHaveAttribute(
      "aria-describedby",
      expect.stringContaining("error"),
    );
    expect(screen.getByRole("alert").previousElementSibling).toHaveClass(
      "reminder-inputs",
    );
    expect(fetch).not.toHaveBeenCalled();
  },
);
it("blank draft and a suggestion do not create a reminder or Telegram helper", () => {
  setup(false, false, false);
  expect(remove()).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Написать рекрутеру" }));
  expect(action()).toHaveValue("Написать рекрутеру");
  expect(screen.queryByLabelText("Дата напоминания")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Время напоминания")).not.toBeInTheDocument();
  expect(remove()).not.toBeInTheDocument();
  expect(screen.queryByText(/Без Telegram/)).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});
it("full unsaved draft has no persisted-remove button and calm disconnected helper", () => {
  setup();
  change(date(), "2026-10-13");
  change(time(), "12:00");
  expect(remove()).not.toBeInTheDocument();
  const helper = screen.getByText(
    /Без Telegram это напоминание будет видно только в Job Hunter AI/,
  );
  expect(helper).toHaveTextContent(
    "Подключите Telegram, чтобы получать уведомления.",
  );
  expect(helper).toHaveClass("muted");
  expect(helper).not.toHaveClass("status-error");
});
it("saved reminder removal stays available during editing and mutates only on Save", () => {
  setup(true);
  expect(remove()).toBeInTheDocument();
  change(time(), "13:00");
  expect(remove()).toBeInTheDocument();
  fireEvent.click(remove()!);
  expect(date()).toHaveValue("");
  expect(time()).toHaveValue("");
  expect(remove()).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});
it.each([false, true])(
  "Cancel restores persisted state (exact=%s)",
  (exact) => {
    setup(exact);
    change(date(), "2026-10-14");
    change(time(), "13:00");
    change(action(), "Draft");
    fireEvent.click(group().getByRole("button", { name: "Отмена" }));
    fireEvent.click(screen.getByRole("button", { name: "Следующее действие" }));
    expect(action()).toHaveValue("Подготовить отклик");
    if (exact) {
      expect(date()).toHaveValue("2026-10-13");
      expect(time()).toHaveValue("12:00");
    } else {
      expect(
        screen.queryByLabelText("Дата напоминания"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByLabelText("Время напоминания"),
      ).not.toBeInTheDocument();
    }
    expect(group().getByRole("button", { name: "Сохранить" })).toBeDisabled();
  },
);
it("reminder without action owns action error only", async () => {
  setup();
  change(action(), "");
  change(date(), "2026-10-13");
  change(time(), "12:00");
  await save();
  expect(action()).toHaveAttribute("aria-invalid", "true");
  expect(action()).toHaveAttribute(
    "aria-describedby",
    expect.stringContaining("application-next_action-error"),
  );
  expect(date()).toHaveAttribute("aria-invalid", "false");
  expect(time()).toHaveAttribute("aria-invalid", "false");
  expect(fetch).not.toHaveBeenCalled();
});
it.each(["a".repeat(501), "bad\0action"])(
  "invalid action text owns only textarea error",
  async (value) => {
    setup();
    change(action(), value);
    await save();
    expect(action()).toHaveAttribute("aria-invalid", "true");
    expect(date()).toHaveAttribute("aria-invalid", "false");
    expect(time()).toHaveAttribute("aria-invalid", "false");
    expect(fetch).not.toHaveBeenCalled();
  },
);
it.each(["", "Invalid/Zone"])(
  "missing/invalid timezone belongs to timezone control: %s",
  async (zone) => {
    clock.zone = zone;
    setup();
    change(date(), "2026-10-13");
    change(time(), "12:00");
    await save();
    expect(
      screen.getByRole("combobox", { name: "Выберите часовой пояс" }),
    ).toHaveAttribute("aria-invalid", "true");
    expect(
      screen.getByRole("combobox", { name: "Выберите часовой пояс" }),
    ).toHaveAttribute(
      "aria-describedby",
      "application-next_action-reminder-error",
    );
    expect(action()).toHaveAttribute("aria-invalid", "false");
    expect(date()).toHaveAttribute("aria-invalid", "false");
    expect(fetch).not.toHaveBeenCalled();
  },
);
it.each(["REMINDER_TOO_SOON", "REMINDER_DATETIME_INVALID"])(
  "API %s marks reminder controls only and retains draft",
  async (code) => {
    setup();
    change(date(), "2026-10-13");
    change(time(), "12:00");
    vi.mocked(fetch).mockResolvedValue(
      Response.json({ code }, { status: 422 }),
    );
    await save();
    expect(action()).toHaveAttribute("aria-invalid", "false");
    expect(date()).toHaveAttribute("aria-invalid", "true");
    expect(time()).toHaveAttribute("aria-invalid", "true");
    expect(date()).toHaveValue("2026-10-13");
    expect(time()).toHaveValue("12:00");
  },
);
it("connected helper promises Telegram delivery before Save", () => {
  setup(false, true);
  change(date(), "2026-10-13");
  change(time(), "12:00");
  expect(
    screen.getByText("Уведомление придёт в Telegram."),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Без Telegram/)).not.toBeInTheDocument();
});
it.each(["pending", "claimed"] as const)(
  "saved %s separates disconnected schedule from connected notification",
  (state) => {
    const { rerender } = render(
      <ReminderSummary
        value={{ ...schedule, reminder_delivery_state: state }}
        telegramLinked={false}
      />,
    );
    expect(
      screen.getByText("Запланировано только в Job Hunter AI"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Уведомление запланировано"),
    ).not.toBeInTheDocument();
    rerender(
      <ReminderSummary
        value={{ ...schedule, reminder_delivery_state: state }}
        telegramLinked={true}
      />,
    );
    expect(screen.getByText("Уведомление запланировано")).toBeInTheDocument();
  },
);
it("successful reminder Save exposes persisted removal on reopening", async () => {
  const application = setup();
  change(date(), "2026-10-13");
  change(time(), "12:00");
  vi.mocked(fetch).mockResolvedValue(
    Response.json({ ...saved, application: { ...application, ...schedule } }),
  );
  await save();
  expect(
    screen.getByText("Запланировано только в Job Hunter AI"),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Следующее действие" }));
  expect(remove()).toBeInTheDocument();
});

it("empty reminder is compact: Add visible, fields, timezone and helper hidden", () => {
  setup(false, false, false);
  const add = screen.getByRole("button", { name: "+ Добавить напоминание" });
  expect(add).toHaveAttribute("aria-expanded", "false");
  expect(add).toHaveAttribute(
    "aria-controls",
    "application-next_action-reminder",
  );
  expect(
    document.getElementById("application-next_action-reminder"),
  ).not.toBeVisible();
  expect(screen.queryByLabelText("Дата напоминания")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Время напоминания")).not.toBeInTheDocument();
  expect(screen.queryByText(/Часовой пояс/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Без Telegram/)).not.toBeInTheDocument();
});
it.each([false, true])(
  "Add reveals fields and correct helper without mutation or dirty state (linked=%s)",
  (linked) => {
    setup(false, linked, false);
    fireEvent.click(
      screen.getByRole("button", { name: "+ Добавить напоминание" }),
    );
    expect(date()).toBeVisible();
    expect(time()).toBeVisible();
    expect(screen.getByText("Часовой пояс: Asia/Yerevan")).toBeVisible();
    expect(
      screen.getByText(
        linked ? "Уведомление придёт в Telegram." : /Без Telegram/,
      ),
    ).toBeVisible();
    expect(group().getByRole("button", { name: "Сохранить" })).toBeDisabled();
    expect(remove()).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  },
);
it("Cancel after empty reveal restores collapsed optional reminder", () => {
  setup();
  fireEvent.click(group().getByRole("button", { name: "Отмена" }));
  fireEvent.click(screen.getByRole("button", { name: "Следующее действие" }));
  expect(
    screen.getByRole("button", { name: "+ Добавить напоминание" }),
  ).toBeVisible();
  expect(screen.queryByLabelText("Дата напоминания")).not.toBeInTheDocument();
  expect(screen.queryByText(/Без Telegram/)).not.toBeInTheDocument();
});
it.each(["Enter", " ", "click"])(
  "user reveal focuses date after %s activation without mutation",
  (activation) => {
    setup(false, false, false);
    const add = screen.getByRole("button", {
      name: "+ Добавить напоминание",
    });
    add.focus();
    expect(add).toHaveFocus();
    if (activation !== "click") {
      fireEvent.keyDown(add, { key: activation });
      fireEvent.keyUp(add, { key: activation });
    }
    // JSDOM does not synthesize the native button click from Enter/Space.
    fireEvent.click(add);
    expect(date()).toHaveFocus();
    expect(group().getByRole("button", { name: "Сохранить" })).toBeDisabled();
    expect(fetch).not.toHaveBeenCalled();
  },
);
it("opening a persisted reminder keeps header focus instead of autofocus", () => {
  setup(true);
  expect(date()).not.toHaveFocus();
  const header = screen.getByRole("button", { name: "Следующее действие" });
  header.focus();
  fireEvent.click(header);
  fireEvent.click(header);
  expect(date()).toBeVisible();
  expect(header).toHaveFocus();
});
it("draft rerenders after reveal do not steal focus from another control", () => {
  setup();
  time().focus();
  change(time(), "13:00");
  change(action(), "Проверить новый ответ");
  expect(time()).toHaveFocus();
});
it("persisted reminder reveals immediately without duplicate date or schedule summary while editing", () => {
  setup(true);
  expect(date()).toBeVisible();
  expect(time()).toBeVisible();
  expect(remove()).toBeVisible();
  expect(remove()).toHaveClass("text-action");
  expect(
    screen.queryByRole("button", { name: "+ Добавить напоминание" }),
  ).not.toBeInTheDocument();
  expect(document.querySelector(".reminder-summary")).not.toBeInTheDocument();
  expect(document.querySelector(".reminder-delivery")).not.toBeInTheDocument();
  expect(
    screen.queryByText("Запланировано только в Job Hunter AI"),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Следующее действие" }));
  expect(document.querySelector(".reminder-summary")).toBeVisible();
  expect(
    screen.getByText("Запланировано только в Job Hunter AI"),
  ).toBeVisible();
  expect(screen.queryByText(/Без Telegram/)).not.toBeVisible();
});
it("Done is an action-level control outside the reminder section", () => {
  setup(true);
  const done = screen.getByRole("button", { name: "Выполнено" });
  expect(done.closest(".application-notes-actions")).not.toBeNull();
  expect(done.closest("#application-next_action-reminder")).toBeNull();
  change(time(), "13:00");
  expect(done).toBeDisabled();
});
it.each([
  ["sent", null, "Напоминание отправлено"],
  [
    "failed",
    "uncertain",
    "Не удалось подтвердить отправку. Напоминание могло прийти.",
  ],
  ["failed", "unavailable", "Не удалось отправить напоминание"],
  [
    "failed",
    "telegram_not_connected",
    "Напоминание не отправлено: Telegram не подключён.",
  ],
] as const)(
  "editing %s/%s keeps delivery information once without repeated date/timezone",
  (state, reason, copy) => {
    render(
      <ApplicationNotesEditor
        application={{
          ...saved.application,
          ...schedule,
          next_action_due_on: null,
          reminder_delivery_state: state,
          reminder_failure_reason: reason,
        }}
        onSaved={vi.fn()}
        telegramLinked={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Следующее действие" }));
    expect(screen.getAllByText(copy)).toHaveLength(1);
    expect(document.querySelector(".reminder-summary")).not.toBeInTheDocument();
    expect(document.querySelector("time")).not.toBeInTheDocument();
    expect(screen.getAllByText(/Asia\/Yerevan/)).toHaveLength(1);
    if (reason === "uncertain")
      expect(
        screen.getAllByText("Автоматически повторять не будем."),
      ).toHaveLength(1);
  },
);
