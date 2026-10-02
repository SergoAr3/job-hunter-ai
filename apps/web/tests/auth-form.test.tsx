import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthForm } from "../components/auth-form";
import { WebError } from "../lib/errors";
const transport = vi.hoisted(() => vi.fn());
vi.mock("../lib/client", () => ({ webRequest: transport }));
afterEach(() => transport.mockReset());
it.each(["login", "register"] as const)(
  "native accessible inputs use correct autocomplete for %s",
  (mode) => {
    render(<AuthForm mode={mode} next="/profile" />);
    expect(screen.getByLabelText("Email")).toHaveAttribute(
      "autocomplete",
      "email",
    );
    expect(screen.getByLabelText("Пароль")).toHaveAttribute(
      "autocomplete",
      mode === "login" ? "current-password" : "new-password",
    );
    if (mode === "login")
      expect(
        screen.getByRole("button", { name: "Войти через Telegram" }),
      ).toBeInTheDocument();
    else
      expect(
        screen.queryByRole("button", { name: /Telegram/ }),
      ).not.toBeInTheDocument();
  },
);
it.each([14, 129])(
  "enforces %s-character password without sending a request",
  (length) => {
    render(<AuthForm mode="register" next="/profile" />);
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "b@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Пароль"), {
      target: { value: "a".repeat(length) },
    });
    fireEvent.submit(
      screen.getByRole("button", { name: "Создать аккаунт" }).closest("form")!,
    );
    expect(transport).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Пароль")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  },
);
it("guards duplicate submits, preserves Unicode/spaces and clears password on failure", async () => {
  let reject!: (error: unknown) => void;
  transport.mockImplementation(
    () =>
      new Promise((_resolve, r) => {
        reject = r;
      }),
  );
  render(<AuthForm mode="login" next="//evil.example" />);
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "b@example.com" },
  });
  const password = "  Unicode пароль 🔑  ";
  fireEvent.change(screen.getByLabelText("Пароль"), {
    target: { value: password },
  });
  const form = screen.getByRole("button", { name: "Войти" }).closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(transport).toHaveBeenCalledTimes(1);
  expect(JSON.parse(transport.mock.calls[0][1].body).password).toBe(password);
  expect(screen.getByRole("button", { name: "Подождите…" })).toBeDisabled();
  reject(new WebError("auth_invalid_credentials", 401));
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  expect(screen.getByLabelText("Пароль")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Войти" })).toBeEnabled();
  expect(
    screen.getByRole("link", { name: "Зарегистрироваться" }),
  ).toHaveAttribute("href", "/register?next=%2Fprofile");
});
it("uses neutral registration result and discloses unconfirmed logout", () => {
  render(<AuthForm mode="login" next="/applications" registered localLogout />);
  expect(
    screen.getByText(/Запрос на регистрацию обработан/),
  ).toBeInTheDocument();
  expect(screen.getByText(/может оставаться активной/)).toBeInTheDocument();
});

it("Telegram social login follows primary submit, with decorative icon and accessible label", () => {
  render(<AuthForm mode="login" next="/profile" />);
  const primary = screen.getByRole("button", { name: "Войти" });
  const telegram = screen.getByRole("button", { name: "Войти через Telegram" });
  expect(
    primary.compareDocumentPosition(telegram) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(telegram).toHaveClass("telegram-login-button");
  expect(telegram).toHaveAttribute("type", "button");
  expect(telegram.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  expect(telegram.closest("section")).toBe(
    primary.closest("form")?.nextElementSibling,
  );
});
