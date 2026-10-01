import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApplicationStatusDetail } from "../components/application-status-detail";
import { saved } from "./fixtures";

const detail = (status: string) => ({
  ...saved,
  application: { ...saved.application, status },
});
const response = (value: unknown, status = 200) =>
  Response.json(value, { status });
const statusControl = () => screen.getByRole("combobox", { name: /^Статус / });
function chooseStatus(label: string) {
  fireEvent.click(statusControl());
  fireEvent.click(screen.getByRole("option", { name: label }));
}

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => vi.unstubAllGlobals());

it("selects the current status, offers all eight choices and skips the same status", () => {
  render(<ApplicationStatusDetail detail={saved} />);
  const select = statusControl();
  expect(select).toHaveTextContent("Собеседование");
  fireEvent.click(select);
  expect(screen.getAllByRole("option")).toHaveLength(8);
  expect(screen.getByRole("option", { name: "Собеседование" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const save = screen.getByRole("button", { name: "Сохранить статус" });
  expect(save).toBeDisabled();
  fireEvent.click(screen.getByRole("option", { name: "Отклик отправлен" }));
  expect(save).toBeEnabled();
  expect(document.querySelector(".status-badge")).toHaveTextContent(
    "Собеседование",
  );
});

it("locks double submit and confirms actual status with a fresh GET", async () => {
  let finishPut!: (value: Response) => void;
  const fetcher = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finishPut = resolve;
        }),
    )
    .mockImplementationOnce(() => Promise.resolve(response(detail("offer"))));
  vi.stubGlobal("fetch", fetcher);
  render(<ApplicationStatusDetail detail={saved} />);
  const select = statusControl();
  chooseStatus("Отклик отправлен");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить статус" }));
  expect(select).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Сохранить статус" }),
  ).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("Сохраняем статус");
  fireEvent.submit(select.closest("form")!);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe("/api/applications/42/status");
  expect(fetcher.mock.calls[0][1].method).toBe("PUT");
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    status: "applied",
  });
  finishPut(response({ ok: true }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(fetcher.mock.calls[1][0]).toBe("/api/applications/42");
  await waitFor(() =>
    expect(document.querySelector(".status-badge")).toHaveTextContent("Оффер"),
  );
  expect(select).toHaveTextContent("Оффер");
  expect(screen.getByRole("status")).toHaveTextContent(
    "Показан актуальный статус из API",
  );
});

it("does not claim success when the confirming GET fails, and refresh only reads", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(response({ ok: true }))
    .mockResolvedValueOnce(response({ code: "api_unavailable" }, 503))
    .mockResolvedValueOnce(response(detail("applied")));
  vi.stubGlobal("fetch", fetcher);
  render(<ApplicationStatusDetail detail={saved} />);
  chooseStatus("Отклик отправлен");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить статус" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "актуальный статус не удалось подтвердить",
  );
  expect(document.querySelector(".status-badge")).toHaveTextContent(
    "Статус не подтверждён",
  );
  expect(
    screen.queryByText("Статус сохранён и подтверждён."),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Сохранить статус" }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Обновить данные" }));
  await waitFor(() =>
    expect(document.querySelector(".status-badge")).toHaveTextContent(
      "Отклик отправлен",
    ),
  );
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(fetcher.mock.calls.map((call) => call[1]?.method ?? "GET")).toEqual([
    "PUT",
    "GET",
    "GET",
  ]);
});

it("treats a PUT network failure as ambiguous and never retries the mutation", async () => {
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new Error("timeout"))
    .mockResolvedValueOnce(response(detail("applied")));
  vi.stubGlobal("fetch", fetcher);
  render(<ApplicationStatusDetail detail={saved} />);
  chooseStatus("Отклик отправлен");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить статус" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Проверьте актуальное состояние",
  );
  expect(document.querySelector(".status-badge")).toHaveTextContent(
    "Статус не подтверждён",
  );
  fireEvent.click(screen.getByRole("button", { name: "Обновить данные" }));
  await waitFor(() =>
    expect(document.querySelector(".status-badge")).toHaveTextContent(
      "Отклик отправлен",
    ),
  );
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][0]).toBe("/api/applications/42");
});

it("shows neutral not-found and safe 422 feedback", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(response({ code: "status_invalid" }, 422));
  vi.stubGlobal("fetch", fetcher);
  const { unmount } = render(<ApplicationStatusDetail detail={saved} />);
  chooseStatus("Оффер");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить статус" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Выберите допустимое значение",
  );
  expect(
    screen.getByRole("button", { name: "Сохранить статус" }),
  ).toBeEnabled();
  unmount();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(response({ code: "APPLICATION_NOT_FOUND" }, 404)),
  );
  render(<ApplicationStatusDetail detail={saved} />);
  chooseStatus("Оффер");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить статус" }));
  expect(
    await screen.findByRole("heading", { name: "Вакансия не найдена" }),
  ).toBeInTheDocument();
});
