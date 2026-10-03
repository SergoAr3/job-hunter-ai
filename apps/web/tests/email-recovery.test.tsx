import { StrictMode } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { EmailRecovery, EmailRequestForm } from "../components/email-recovery";
import { AuthForm } from "../components/auth-form";
import { WebError } from "../lib/errors";
const transport = vi.hoisted(() => vi.fn());
vi.mock("../lib/client", () => ({ webRequest: transport }));
const token = "t".repeat(43);
beforeEach(() =>
  window.history.replaceState(null, "", "/verify-email#token=" + token),
);
afterEach(() => {
  transport.mockReset();
  window.history.replaceState(null, "", "/");
});
it("cleans fragment without auto consume, survives StrictMode and confirms without session", async () => {
  transport.mockResolvedValue({ ok: true });
  render(
    <StrictMode>
      <EmailRecovery mode="verify" />
    </StrictMode>,
  );
  const button = await screen.findByRole("button", {
    name: "Подтвердить email",
  });
  expect(window.location.hash).toBe("");
  expect(transport).not.toHaveBeenCalled();
  fireEvent.click(button);
  await screen.findByText(/Email подтверждён/);
  expect(transport).toHaveBeenCalledTimes(1);
  expect(transport.mock.calls[0][0]).toBe("/api/auth/email/verify");
  expect(JSON.parse(transport.mock.calls[0][1].body)).toEqual({ token });
  expect(
    screen.getByRole("link", { name: "Вернуться ко входу" }),
  ).toHaveAttribute("href", "/login");
});
it.each([
  "EMAIL_TOKEN_INVALID",
  "EMAIL_TOKEN_EXPIRED",
  "EMAIL_TOKEN_USED",
  "rate_limited",
])("shows safe %s state and resend", async (code) => {
  transport.mockRejectedValue(new WebError(code, 400));
  render(<EmailRecovery mode="verify" />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Подтвердить email" }),
  );
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Отправить письмо повторно" }),
  ).toBeInTheDocument();
  expect(window.location.hash).toBe("");
});
it.each(["", "#token=invalid", `#token=${token}&token=${token}`])(
  "rejects missing/malformed/duplicate fragment %s",
  async (hash) => {
    window.history.replaceState(null, "", "/verify-email" + hash);
    render(<EmailRecovery mode="verify" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Ссылка недействительна/,
    );
    expect(
      screen.queryByRole("button", { name: "Подтвердить email" }),
    ).not.toBeInTheDocument();
    expect(transport).not.toHaveBeenCalled();
  },
);
it("reset mismatch validates without consume, preserves Unicode/spaces, clears passwords and guards double submit", async () => {
  let finish!: (value: unknown) => void;
  transport.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  window.history.replaceState(null, "", "/reset-password#token=" + token);
  render(<EmailRecovery mode="reset" />);
  const button = await screen.findByRole("button", {
    name: "Сохранить пароль",
  });
  const password = "  Unicode пароль 🔒  ";
  fireEvent.change(screen.getByLabelText("Новый пароль"), {
    target: { value: password },
  });
  fireEvent.change(screen.getByLabelText("Повторите пароль"), {
    target: { value: "different password" },
  });
  fireEvent.submit(button.closest("form")!);
  expect(screen.getByRole("alert")).toHaveTextContent("Пароли не совпадают");
  expect(transport).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Повторите пароль"), {
    target: { value: password },
  });
  const form = button.closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(transport).toHaveBeenCalledTimes(1);
  expect(JSON.parse(transport.mock.calls[0][1].body)).toEqual({
    token,
    password,
  });
  finish({ ok: true });
  await screen.findByText(/Все прежние сессии завершены/);
  expect((form.elements.namedItem("password") as HTMLInputElement).value).toBe(
    "",
  );
  expect(
    (form.elements.namedItem("confirmation") as HTMLInputElement).value,
  ).toBe("");
  expect(window.location.hash).toBe("");
});
it("reset failure clears both password fields and offers replacement link", async () => {
  transport.mockRejectedValue(new WebError("EMAIL_TOKEN_EXPIRED", 400));
  render(<EmailRecovery mode="reset" />);
  const button = await screen.findByRole("button", {
    name: "Сохранить пароль",
  });
  for (const label of ["Новый пароль", "Повторите пароль"])
    fireEvent.change(screen.getByLabelText(label), {
      target: { value: "replacement password 123" },
    });
  fireEvent.click(button);
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Новый пароль")).toHaveValue("");
  expect(screen.getByLabelText("Повторите пароль")).toHaveValue("");
  expect(
    screen.getByRole("link", { name: "Запросить новую ссылку" }),
  ).toHaveAttribute("href", "/forgot-password");
});
it.each(["verify", "reset"] as const)(
  "%s requests show generic confirmation",
  async (purpose) => {
    transport.mockResolvedValue({ ok: true });
    render(<EmailRequestForm purpose={purpose} email="b@example.com" />);
    fireEvent.submit(screen.getByRole("button").closest("form")!);
    expect(await screen.findByRole("status")).toHaveTextContent(/Если аккаунт/);
    expect(JSON.parse(transport.mock.calls[0][1].body)).toEqual({
      email: "b@example.com",
    });
  },
);
it("verification-required login offers resend and forgot password without redirect or credential reuse", async () => {
  transport
    .mockRejectedValueOnce(new WebError("EMAIL_VERIFICATION_REQUIRED", 403))
    .mockResolvedValue({ ok: true });
  render(<AuthForm mode="login" next="/profile" />);
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "b@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Пароль"), {
    target: { value: "password value 12345" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Войти" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Подтвердите email",
  );
  expect(screen.getByLabelText("Пароль")).toHaveValue("");
  const resend = screen.getByRole("button", {
    name: "Отправить письмо повторно",
  });
  fireEvent.click(resend);
  await screen.findByText(/Если аккаунт существует/);
  expect(JSON.parse(transport.mock.calls[1][1].body)).toEqual({
    email: "b@example.com",
  });
  expect(screen.getByRole("link", { name: "Забыли пароль?" })).toHaveAttribute(
    "href",
    "/forgot-password",
  );
});

const secondToken = "b".repeat(43);
const modes = ["verify", "reset"] as const;
const action = (mode: (typeof modes)[number]) =>
  mode === "verify" ? "Подтвердить email" : "Сохранить пароль";
const successText = (mode: (typeof modes)[number]) =>
  mode === "verify" ? /Email подтверждён/ : /Все прежние сессии завершены/;
function fillReset(password = "replacement password 123") {
  for (const label of ["Новый пароль", "Повторите пароль"])
    fireEvent.change(screen.getByLabelText(label), {
      target: { value: password },
    });
}
async function incomingProof(raw: string) {
  // Exercise the actual browser event, without sleeps or a second synthetic event.
  await act(async () => {
    const changed = new Promise<void>((resolve) =>
      window.addEventListener("hashchange", () => resolve(), { once: true }),
    );
    window.location.hash = "token=" + raw;
    await changed;
  });
}
function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

it.each(modes)(
  "%s captures initial and same-tab proof, preserving query and focus",
  async (mode) => {
    window.history.replaceState(
      null,
      "",
      `/${mode === "verify" ? "verify-email" : "reset-password"}?next=%2Fprofile#token=${token}`,
    );
    transport.mockResolvedValue({ ok: true });
    render(
      <StrictMode>
        <EmailRecovery mode={mode} />
      </StrictMode>,
    );
    const button = await screen.findByRole("button", { name: action(mode) });
    expect(window.location.hash).toBe("");
    expect(window.location.search).toBe("?next=%2Fprofile");
    button.focus();
    await incomingProof(secondToken);
    expect(window.location.hash).toBe("");
    expect(window.location.search).toBe("?next=%2Fprofile");
    expect(button).toHaveFocus();
    expect(transport).not.toHaveBeenCalled();
    if (mode === "reset") fillReset();
    fireEvent.click(screen.getByRole("button", { name: action(mode) }));
    await screen.findByText(successText(mode));
    expect(JSON.parse(transport.mock.calls[0][1].body).token).toBe(secondToken);
  },
);

it.each(
  modes.flatMap((mode) =>
    [
      "EMAIL_TOKEN_INVALID",
      "EMAIL_TOKEN_EXPIRED",
      "EMAIL_TOKEN_USED",
      "auth_unavailable",
    ].map((code) => ({ mode, code })),
  ),
)(
  "$mode replaces stale $code with a usable new proof",
  async ({ mode, code }) => {
    transport
      .mockRejectedValueOnce(new WebError(code, 400))
      .mockResolvedValue({ ok: true });
    render(<EmailRecovery mode={mode} />);
    const button = await screen.findByRole("button", { name: action(mode) });
    if (mode === "reset") fillReset();
    fireEvent.click(button);
    await screen.findByRole("alert");
    await incomingProof(secondToken);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(window.location.hash).toBe("");
    if (mode === "reset") {
      expect(screen.getByLabelText("Новый пароль")).toHaveValue("");
      fillReset();
    }
    fireEvent.click(screen.getByRole("button", { name: action(mode) }));
    await screen.findByText(successText(mode));
    expect(transport).toHaveBeenCalledTimes(2);
    expect(JSON.parse(transport.mock.calls[1][1].body).token).toBe(secondToken);
  },
);

it.each(modes)("%s accepts a new proof after success", async (mode) => {
  transport.mockResolvedValue({ ok: true });
  render(<EmailRecovery mode={mode} />);
  const button = await screen.findByRole("button", { name: action(mode) });
  if (mode === "reset") fillReset();
  fireEvent.click(button);
  await screen.findByText(successText(mode));
  await incomingProof(secondToken);
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: action(mode) })).toBeEnabled();
  if (mode === "reset") fillReset();
  fireEvent.click(screen.getByRole("button", { name: action(mode) }));
  await screen.findByText(successText(mode));
  expect(JSON.parse(transport.mock.calls[1][1].body).token).toBe(secondToken);
});

it.each(
  modes.flatMap((mode) =>
    ["success", "error"].map((outcome) => ({ mode, outcome })),
  ),
)(
  "$mode ignores late A $outcome after activating B, even if abort is ignored",
  async ({ mode, outcome }) => {
    const old = deferred();
    transport.mockReturnValueOnce(old.promise).mockResolvedValue({ ok: true });
    render(<EmailRecovery mode={mode} />);
    const button = await screen.findByRole("button", { name: action(mode) });
    if (mode === "reset") fillReset();
    fireEvent.click(button);
    expect(screen.getByRole("button", { name: "Подождите…" })).toBeDisabled();
    const oldSignal = transport.mock.calls[0][1].signal as AbortSignal;
    await incomingProof(secondToken);
    expect(oldSignal.aborted).toBe(true);
    expect(window.location.hash).toBe("");
    expect(screen.getByRole("button", { name: action(mode) })).toBeEnabled();
    if (mode === "reset") fillReset("new attempt password 456");
    await act(async () => {
      if (outcome === "success") old.resolve({ ok: true });
      else old.reject(new WebError("EMAIL_TOKEN_EXPIRED", 400));
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    if (mode === "reset")
      expect(screen.getByLabelText("Новый пароль")).toHaveValue(
        "new attempt password 456",
      );
    fireEvent.click(screen.getByRole("button", { name: action(mode) }));
    await screen.findByText(successText(mode));
    expect(transport).toHaveBeenCalledTimes(2);
    expect(JSON.parse(transport.mock.calls[0][1].body).token).toBe(token);
    expect(JSON.parse(transport.mock.calls[1][1].body).token).toBe(secondToken);
  },
);

it.each(modes)(
  "%s late A finally does not unlock or abort pending B",
  async (mode) => {
    const old = deferred(),
      current = deferred();
    transport
      .mockReturnValueOnce(old.promise)
      .mockReturnValueOnce(current.promise);
    render(<EmailRecovery mode={mode} />);
    const button = await screen.findByRole("button", { name: action(mode) });
    if (mode === "reset") fillReset();
    fireEvent.click(button);
    await incomingProof(secondToken);
    if (mode === "reset") fillReset("new attempt password 456");
    fireEvent.click(screen.getByRole("button", { name: action(mode) }));
    const form = screen
      .getByRole("button", { name: "Подождите…" })
      .closest("form")!;
    await act(async () => old.resolve({ ok: true }));
    expect(screen.getByRole("button", { name: "Подождите…" })).toBeDisabled();
    expect(transport.mock.calls[1][1].signal.aborted).toBe(false);
    if (mode === "reset")
      expect(screen.getByLabelText("Новый пароль")).toHaveValue(
        "new attempt password 456",
      );
    fireEvent.submit(form);
    expect(transport).toHaveBeenCalledTimes(2);
    await act(async () => current.resolve({ ok: true }));
    await screen.findByText(successText(mode));
  },
);

it("removes hashchange listeners on unmount and StrictMode remount has one live handler", async () => {
  const add = vi.spyOn(window, "addEventListener"),
    remove = vi.spyOn(window, "removeEventListener");
  const replace = vi.spyOn(window.history, "replaceState");
  try {
    const first = render(
      <StrictMode>
        <EmailRecovery mode="verify" />
      </StrictMode>,
    );
    await screen.findByRole("button", { name: action("verify") });
    const handlers = add.mock.calls
      .filter(([name]) => name === "hashchange")
      .map(([, handler]) => handler);
    expect(handlers).toHaveLength(2);
    expect(remove).toHaveBeenCalledWith("hashchange", handlers[0]);
    first.unmount();
    expect(remove).toHaveBeenCalledWith("hashchange", handlers[1]);
    window.history.replaceState(null, "", "/verify-email#token=" + token);
    const second = render(<EmailRecovery mode="verify" />);
    await screen.findByRole("button", { name: action("verify") });
    replace.mockClear();
    await incomingProof(secondToken);
    expect(replace).toHaveBeenCalledTimes(1);
    transport.mockResolvedValue({ ok: true });
    fireEvent.click(screen.getByRole("button", { name: action("verify") }));
    await screen.findByText(successText("verify"));
    expect(transport).toHaveBeenCalledTimes(1);
    second.unmount();
  } finally {
    add.mockRestore();
    remove.mockRestore();
    replace.mockRestore();
  }
});
