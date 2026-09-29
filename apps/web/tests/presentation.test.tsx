import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { compactLocation } from "../lib/presentation";
import { JobMetadata, JobText, Salary, Matching } from "../components/vacancy";
import { vacancy } from "./fixtures";
describe("display-only location", () => {
  it.each([
    [null, ""],
    [" , , ", ""],
    ["Москва, Москва, ул. Лесная, 1", "Москва, ул. Лесная"],
    [
      " Московская область, московская область, Химки, улица 1",
      "Московская область, Химки",
    ],
    ["Регион, город, адрес", "Регион, город"],
    ["Неизвестный формат без запятых", "Неизвестный формат без запятых"],
  ])("formats %s conservatively", (raw, expected) =>
    expect(compactLocation(raw)).toBe(expected),
  );
  it("retains full source location in an accessible disclosure", () => {
    const raw = "Москва, Москва, улица Лесная, дом 1";
    render(<JobMetadata job={{ ...vacancy, location: raw }} />);
    expect(screen.getByText("Москва, улица Лесная")).toHaveAccessibleName(
      "Москва, улица Лесная",
    );
    fireEvent.click(screen.getByText("Москва, улица Лесная"));
    expect(screen.getByText(raw)).toBeVisible();
  });
});
describe("salary presentation", () => {
  it("groups confirmed amounts and maps only confirmed RUB to its symbol", () => {
    render(
      <Salary
        job={{
          salary_text: "от 260000",
          salary_min: 260000,
          salary_max: null,
          salary_currency: "RUB",
        }}
      />,
    );
    expect(screen.getByText(/от 260\s000 ₽/)).toBeInTheDocument();
  });
  it("never invents currency or a period", () => {
    const { container } = render(
      <Salary
        job={{
          salary_text: null,
          salary_min: 260000,
          salary_max: null,
          salary_currency: null,
        }}
      />,
    );
    expect(container).toHaveTextContent("от 260 000");
    expect(container.textContent).not.toMatch(/₽|RUB|месяц|год/);
  });
  it("preserves source salary text when no amounts are provided, and renders no unknown zero", () => {
    const { rerender, container } = render(
      <Salary job={{ ...vacancy, salary_text: "По договорённости" }} />,
    );
    expect(container).toHaveTextContent("По договорённости");
    rerender(<Salary job={vacancy} />);
    expect(container).toBeEmptyDOMElement();
  });
  it.each([
    { salary_min: 0, salary_max: null, salary_text: null },
    { salary_min: null, salary_max: 0, salary_text: null },
    { salary_min: 0, salary_max: 0, salary_text: "от 0 ₽" },
  ])("hides zero numeric salary values", (salary) => {
    const { container } = render(
      <Salary job={{ ...vacancy, ...salary, salary_currency: "RUB" }} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(container).not.toHaveTextContent(/0|₽/);
  });
  it("keeps positive bounds when the other bound is zero", () => {
    const { container } = render(
      <Salary
        job={{
          ...vacancy,
          salary_min: 0,
          salary_max: 260000,
          salary_currency: "RUB",
        }}
      />,
    );
    expect(container).toHaveTextContent("до 260 000 ₽");
    expect(container).not.toHaveTextContent(/до 0(?: ₽)?/);
  });
});
it("preserves source paragraph content as safe text", () => {
  const { container } = render(
    <JobText
      job={{ ...vacancy, description: "Первый абзац.\n\nВторой <b>текст</b>." }}
    />,
  );
  expect(screen.getByText("Первый абзац.")).toBeInTheDocument();
  expect(screen.getByText("Второй <b>текст</b>.")).toBeInTheDocument();
  expect(container.querySelector("b")).toBeNull();
});
it("expands API reasons without deriving a score or verdict", () => {
  render(
    <Matching
      preview={{
        ...vacancy.preview_match,
        available: true,
        verdict: "insufficient_data",
        coverage: 25,
        gaps: [
          { code: "role_not_matched", component: "role", value: "Python" },
        ],
      }}
    />,
  );
  expect(
    screen.getByText("Для оценки доступно 25% данных."),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByText(/Различия · 1/));
  expect(screen.getByText("Роль не совпадает: Python")).toBeVisible();
});
it("makes long unmodified source text available through an expandable excerpt", () => {
  const text =
    "Исходный текст вакансии. ".repeat(60) + "Последнее предложение.";
  render(<JobText job={{ ...vacancy, description: text }} />);
  fireEvent.click(screen.getByText("Читать полностью"));
  expect(screen.getByText(text)).toBeVisible();
});
