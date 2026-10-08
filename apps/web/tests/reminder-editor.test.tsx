import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { ApplicationNotesEditor } from "../components/application-notes-editor";
import { ReminderSummary } from "../components/reminder-summary";
import { DashboardFollowUps } from "../components/dashboard-follow-ups";
import { saved } from "./fixtures";

const schedule = {
  next_action_remind_at: "2026-10-10T07:02:00Z",
  next_action_timezone: "Asia/Yerevan",
  reminder_delivery_state: "pending" as const,
  reminder_sent_at: null,
  reminder_failure_reason: null,
};
function setup(exact = false) {
  const application = {
    ...saved.application,
    next_action_due_on: null,
    ...(exact ? schedule : {}),
    next_action_suggestions: [
      {
        id: "follow_up",
        label: "Написать рекрутеру через 5 дней",
        action_text: "Написать рекрутеру через 5 дней",
      },
    ],
  };
  render(
    <ApplicationNotesEditor
      application={application}
      onSaved={vi.fn()}
      telegramLinked={false}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Следующее действие" }));
  return application;
}
const section = () =>
  within(screen.getByRole("region", { name: "Следующее действие" }));
const response = (application: object) =>
  Response.json({ ...saved, application });
beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("suggestion changes only draft, preserves time and blocks Done until Cancel", () => {
  setup(true);
  expect(
    screen.getByText(/Что сделать дальше по этой вакансии/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Подключите Telegram, чтобы получать уведомления/),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Написать рекрутеру через 5 дней" }),
  );
  expect(
    screen.getByRole("textbox", { name: "Следующее действие" }),
  ).toHaveValue("Написать рекрутеру через 5 дней");
  expect(screen.getByLabelText("Время напоминания")).toHaveValue("11:02");
  expect(screen.getByRole("button", { name: "Выполнено" })).toBeDisabled();
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(section().getByRole("button", { name: "Отмена" }));
  expect(
    screen.getByRole("button", { name: "Следующее действие" }),
  ).toHaveFocus();
});
it("saves exact reminder and recovers a lost response by comparing instant and timezone without mutation replay", async () => {
  const application = setup(true);
  fireEvent.change(screen.getByLabelText("Время напоминания"), {
    target: { value: "12:00" },
  });
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new Error("lost"))
    .mockResolvedValueOnce(
      response({
        ...application,
        next_action_remind_at: "2026-10-10T08:00:00Z",
      }),
    );
  vi.stubGlobal("fetch", fetcher);
  await act(async () =>
    fireEvent.click(section().getByRole("button", { name: "Сохранить" })),
  );
  expect(screen.getByLabelText("Время напоминания")).toHaveValue("12:00");
  await act(async () =>
    fireEvent.click(
      screen.getByRole("button", { name: "Проверить сохранение" }),
    ),
  );
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({
    next_action_remind_at: "2026-10-10T12:00:00+04:00",
    next_action_timezone: "Asia/Yerevan",
  });
  expect(fetcher.mock.calls[1][1]?.method ?? "GET").toBe("GET");
  expect(
    screen.getByRole("button", { name: "Следующее действие" }),
  ).toHaveAttribute("aria-expanded", "false");
});
it.each(["clear", "done"])(
  "%s sends minimal partial PATCH and preserves Notes/status",
  async (mode) => {
    const application = setup(true);
    const changes =
      mode === "done"
        ? {
            next_action: null,
            next_action_remind_at: null,
            next_action_timezone: null,
          }
        : { next_action_remind_at: null, next_action_timezone: null };
    const fetcher = vi
      .fn()
      .mockResolvedValue(response({ ...application, ...changes }));
    vi.stubGlobal("fetch", fetcher);
    if (mode === "clear") {
      fireEvent.click(
        screen.getByRole("button", { name: "Убрать напоминание" }),
      );
      await act(async () =>
        fireEvent.click(section().getByRole("button", { name: "Сохранить" })),
      );
    } else
      await act(async () =>
        fireEvent.click(screen.getByRole("button", { name: "Выполнено" })),
      );
    const body = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(body).not.toHaveProperty("note");
    expect(body).not.toHaveProperty("status");
    expect(mode === "done" ? body : body.next_action_remind_at).toEqual(
      mode === "done" ? { next_action: null } : null,
    );
  },
);
it("failed validation keeps draft and associates error with date/time labels", async () => {
  setup(true);
  fireEvent.change(screen.getByLabelText("Время напоминания"), {
    target: { value: "12:00" },
  });
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ code: "REMINDER_TOO_SOON" }, { status: 422 }),
      ),
  );
  await act(async () =>
    fireEvent.click(section().getByRole("button", { name: "Сохранить" })),
  );
  expect(screen.getByLabelText("Время напоминания")).toHaveValue("12:00");
  expect(screen.getByLabelText("Время напоминания")).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(screen.getByRole("alert")).toBeInTheDocument();
});
it("shows sent action and uncertain policy without internal state", () => {
  const { rerender } = render(
    <ReminderSummary
      value={{ ...schedule, reminder_delivery_state: "sent" }}
    />,
  );
  expect(screen.getByText("Напоминание отправлено")).toBeInTheDocument();
  rerender(
    <ReminderSummary
      value={{
        ...schedule,
        reminder_delivery_state: "failed",
        reminder_failure_reason: "uncertain",
      }}
    />,
  );
  expect(
    screen.getByText(
      "Не удалось подтвердить отправку. Напоминание могло прийти.",
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Автоматически повторять не будем."),
  ).toBeInTheDocument();
});
it("loads timezone-authoritative Dashboard buckets and empty state", async () => {
  const fetcher = vi.fn().mockImplementation(async (url: string) =>
    Response.json({
      items: [
        {
          application_id: 42,
          title: "Engineer",
          company: null,
          status: "saved",
          next_action: new URL(url, "http://local").searchParams.get("bucket"),
          next_action_due_on: null,
          due_state: "today",
          ...schedule,
        },
      ],
      has_next: false,
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const { unmount } = render(<DashboardFollowUps />);
  expect(await screen.findByText("Требует внимания")).toBeInTheDocument();
  expect(screen.getByText("Сегодня")).toBeInTheDocument();
  expect(screen.getByText("Далее")).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(
    fetcher.mock.calls.every(([url]) =>
      new URL(url, "http://local").searchParams.get("timezone"),
    ),
  ).toBe(true);
  unmount();
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation(async () =>
        Response.json({ items: [], has_next: false }),
      ),
  );
  render(<DashboardFollowUps />);
  expect(await screen.findByText(/Напоминаний пока нет/)).toBeInTheDocument();
});
