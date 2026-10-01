import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ApplicationStatusDetail } from "../components/application-status-detail";
import { saved } from "./fixtures";

const event = (status: string, occurred_at: string) => ({
  status,
  occurred_at,
});
const oldHistory = {
  items: [
    event("interview", "2026-09-30T14:42:00Z"),
    event("applied", "2026-09-29T13:10:00Z"),
    event("saved", "2026-09-25T21:04:00Z"),
  ],
};
const json = (value: unknown, status = 200) => Response.json(value, { status });
const show = () =>
  screen.getByRole("button", { name: "Показать историю статусов" });
const hide = () =>
  screen.getByRole("button", { name: "Скрыть историю статусов" });
function chooseStatus(label: string) {
  fireEvent.click(screen.getByRole("combobox", { name: /^Статус / }));
  fireEvent.click(screen.getByRole("option", { name: label }));
}

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => vi.unstubAllGlobals());

it("loads only on first disclosure, announces loading, and keeps backend order and labels", async () => {
  let finish!: (value: Response) => void;
  const fetcher = vi.fn().mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<ApplicationStatusDetail detail={saved} />);
  expect(show()).toHaveAttribute("aria-expanded", "false");
  expect(fetcher).not.toHaveBeenCalled();
  show().focus();
  fireEvent.click(show(), { detail: 0 });
  expect(hide()).toHaveAttribute("aria-expanded", "true");
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Загружаем историю статусов",
  );
  expect(fetcher).toHaveBeenCalledOnce();
  finish(json(oldHistory));
  await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(3));
  expect(
    screen.getAllByRole("listitem").map((item) => item.textContent),
  ).toEqual([
    expect.stringContaining("Собеседование"),
    expect.stringContaining("Отклик отправлен"),
    expect.stringContaining("Сохранена"),
  ]);
  const time = within(
    screen.getByRole("region", { name: "История статусов" }),
  ).getAllByRole("time")[0];
  expect(time).toHaveAttribute("datetime", "2026-09-30T14:42:00Z");
  expect(time).toHaveTextContent(/сент\.?/);
  fireEvent.click(hide());
  expect(show()).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(show());
  expect(fetcher).toHaveBeenCalledOnce();
});

it("shows an empty legacy history as a normal state", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ items: [] })));
  render(<ApplicationStatusDetail detail={saved} />);
  fireEvent.click(show());
  expect(
    await screen.findByText("История изменений пока отсутствует."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("retries a failed history GET without a status mutation", async () => {
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new Error("network"))
    .mockResolvedValueOnce(json(oldHistory));
  vi.stubGlobal("fetch", fetcher);
  render(<ApplicationStatusDetail detail={saved} />);
  fireEvent.click(show());
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Не удалось загрузить историю статусов.",
  );
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(3));
  expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
    "/api/applications/42/status-history",
    "/api/applications/42/status-history",
  ]);
});

it("shows the same safe not-found state for unavailable history", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(json({ code: "APPLICATION_NOT_FOUND" }, 404)),
  );
  render(<ApplicationStatusDetail detail={saved} />);
  fireEvent.click(show());
  expect(
    await screen.findByRole("heading", { name: "Вакансия не найдена" }),
  ).toBeInTheDocument();
});

it("does not fetch unopened history after status save", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(json({ ok: true }))
    .mockResolvedValueOnce(
      json({
        ...saved,
        application: { ...saved.application, status: "offer" },
      }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(<ApplicationStatusDetail detail={saved} />);
  chooseStatus("Оффер");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить статус" }));
  await waitFor(() =>
    expect(document.querySelector(".status-badge")).toHaveTextContent("Оффер"),
  );
  expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
    "/api/applications/42/status",
    "/api/applications/42",
  ]);
});

it("refreshes loaded history after confirmed detail GET and displays the new event", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(json(oldHistory))
    .mockResolvedValueOnce(json({ ok: true }))
    .mockResolvedValueOnce(
      json({
        ...saved,
        application: { ...saved.application, status: "offer" },
      }),
    )
    .mockResolvedValueOnce(
      json({
        items: [event("offer", "2026-10-01T09:00:00Z"), ...oldHistory.items],
      }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(<ApplicationStatusDetail detail={saved} />);
  fireEvent.click(show());
  await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(3));
  chooseStatus("Оффер");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить статус" }));
  await waitFor(() =>
    expect(document.querySelector(".status-badge")).toHaveTextContent("Оффер"),
  );
  await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(4));
  expect(screen.getAllByRole("listitem")[0]).toHaveTextContent("Оффер");
  expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
    "/api/applications/42/status-history",
    "/api/applications/42/status",
    "/api/applications/42",
    "/api/applications/42/status-history",
  ]);
  expect(
    screen.getByRole("button", { name: "Сохранить статус" }),
  ).toBeDisabled();
});

it("refreshes history after mutation when the first history GET was still in flight", async () => {
  let finishPut!: (value: Response) => void;
  let finishFirstHistory!: (value: Response) => void;
  const updatedHistory = {
    items: [event("offer", "2026-10-01T09:00:00Z"), ...oldHistory.items],
  };
  let historyGets = 0;
  const fetcher = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith("/status"))
      return new Promise<Response>((resolve) => {
        finishPut = resolve;
      });
    if (url.endsWith("/status-history")) {
      historyGets += 1;
      if (historyGets === 1)
        return new Promise<Response>((resolve) => {
          finishFirstHistory = resolve;
        });
      return Promise.resolve(json(updatedHistory));
    }
    return Promise.resolve(
      json({
        ...saved,
        application: { ...saved.application, status: "offer" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetcher);
  render(<ApplicationStatusDetail detail={saved} />);
  chooseStatus("Оффер");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить статус" }));
  fireEvent.click(show());
  expect(historyGets).toBe(1);
  finishPut(json({ ok: true }));
  await waitFor(() =>
    expect(document.querySelector(".status-badge")).toHaveTextContent("Оффер"),
  );
  expect(historyGets).toBe(1);
  finishFirstHistory(json(oldHistory));
  await waitFor(() => expect(historyGets).toBe(2));
  await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(4));
  expect(screen.getAllByRole("listitem")[0]).toHaveTextContent("Оффер");
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
    "/api/applications/42/status",
    "/api/applications/42/status-history",
    "/api/applications/42",
    "/api/applications/42/status-history",
  ]);
});

it("keeps confirmed status and old events if history refresh fails, then retries only GET", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(json(oldHistory))
    .mockResolvedValueOnce(json({ ok: true }))
    .mockResolvedValueOnce(
      json({
        ...saved,
        application: { ...saved.application, status: "offer" },
      }),
    )
    .mockResolvedValueOnce(json({ code: "api_unavailable" }, 503))
    .mockResolvedValueOnce(
      json({
        items: [event("offer", "2026-10-01T09:00:00Z"), ...oldHistory.items],
      }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(<ApplicationStatusDetail detail={saved} />);
  fireEvent.click(show());
  await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(3));
  chooseStatus("Оффер");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить статус" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Не удалось загрузить историю статусов.",
  );
  expect(document.querySelector(".status-badge")).toHaveTextContent("Оффер");
  expect(
    screen.getByText("Статус сохранён и подтверждён."),
  ).toBeInTheDocument();
  expect(screen.getAllByRole("listitem")).toHaveLength(3);
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(4));
  expect(fetcher.mock.calls.map((call) => call[1]?.method ?? "GET")).toEqual([
    "GET",
    "PUT",
    "GET",
    "GET",
    "GET",
  ]);
});
