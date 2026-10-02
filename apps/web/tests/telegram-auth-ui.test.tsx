import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const transport = vi.hoisted(() => vi.fn());
vi.mock("../lib/client", () => ({ webRequest: transport }));
import { TelegramAuth } from "../components/telegram-auth";
import { AccountMenu } from "../components/account-menu";
import { WebError } from "../lib/errors";
const challenge = {
  token: "T".repeat(43),
  deep_link: "https://t.me/identity_bot?start=auth_" + "T".repeat(43),
  code: "A1B2C3",
  expires_at: new Date(Date.now() + 300000).toISOString(),
  status: "pending",
};
const popup = {
  opener: {},
  closed: false,
  location: { replace: vi.fn() },
  close: vi.fn(),
};
beforeEach(() => {
  popup.opener = {};
  popup.closed = false;
  popup.location.replace.mockReset();
  popup.close.mockReset();
  vi.spyOn(window, "open").mockReturnValue(popup as unknown as Window);
});
afterEach(() => {
  vi.restoreAllMocks();
  transport.mockReset();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
it("offers login, guards duplicate create and shows matching code/deep link", async () => {
  let resolve!: (value: unknown) => void;
  transport.mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  render(
    <StrictMode>
      <TelegramAuth purpose="login" next="/discover?q=C%2B%2B" />
    </StrictMode>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Войти через Telegram" }));
  fireEvent.click(screen.getByRole("button"));
  expect(transport).toHaveBeenCalledTimes(1);
  expect(window.open).toHaveBeenCalledTimes(1);
  expect(window.open).toHaveBeenCalledWith("about:blank", "_blank");
  expect(popup.location.replace).not.toHaveBeenCalled();
  await act(async () => {
    resolve(challenge);
  });
  expect(popup.opener).toBeNull();
  expect(popup.location.replace).toHaveBeenCalledWith(challenge.deep_link);
  expect(screen.getByText("A1B2C3")).toBeInTheDocument();
  expect(screen.getByText("Код сверки:", { exact: false })).toHaveClass(
    "telegram-verification",
  );
  expect(
    screen.getByRole("link", { name: "Открыть Telegram" }),
  ).toHaveAttribute("href", challenge.deep_link);
  expect(screen.getByRole("status")).toHaveTextContent("Ждём подтверждения");
});
it.each(["expired", "cancelled", "conflict"])(
  "poll terminal state %s stops and permits restart",
  async (status) => {
    transport
      .mockResolvedValueOnce(challenge)
      .mockResolvedValueOnce({ status });
    render(<TelegramAuth purpose="login" />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Попробовать снова" }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      status === "conflict"
        ? "другим аккаунтом"
        : status === "expired"
          ? "истёк"
          : "отменён",
    );
    expect(transport).toHaveBeenCalledTimes(2);
  },
);
it("approved status completes once under StrictMode; completion error is safe", async () => {
  transport
    .mockResolvedValueOnce(challenge)
    .mockResolvedValueOnce({ status: "approved" })
    .mockRejectedValueOnce(new WebError("ACCOUNT_LINK_CONFLICT", 409));
  render(
    <StrictMode>
      <TelegramAuth purpose="login" next="//evil.example" />
    </StrictMode>,
  );
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("другим аккаунтом"),
  );
  expect(transport.mock.calls.map((call) => call[0])).toEqual([
    "/api/auth/telegram/challenges",
    "/api/auth/telegram/status",
    "/api/auth/telegram/complete",
  ]);
});
it("link prompts password, preserves spaces, clears it and handles completion outage", async () => {
  transport
    .mockResolvedValueOnce(challenge)
    .mockResolvedValueOnce({ status: "approved" })
    .mockRejectedValueOnce(new WebError("auth_unavailable", 503));
  render(<TelegramAuth purpose="link" />);
  const password = screen.getByLabelText("Подтвердите пароль аккаунта");
  fireEvent.change(password, { target: { value: "  Unicode пароль 🔑  " } });
  fireEvent.submit(screen.getByRole("button").closest("form")!);
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  expect(JSON.parse(transport.mock.calls[0][1].body)).toEqual({
    purpose: "link",
    password: "  Unicode пароль 🔑  ",
  });
  expect(password).toHaveValue("");
});
it.each([false, true])(
  "account shell exposes linked=%s state without internal IDs",
  (linked) => {
    render(<AccountMenu label="Email account" telegramLinked={linked} />);
    if (linked)
      expect(screen.getByText("Telegram подключён")).toBeInTheDocument();
    else
      expect(
        screen.getByRole("button", { name: "Подключить Telegram" }),
      ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Выйти" })).toBeInTheDocument();
  },
);

it.each([
  ["login", "/discover?q=C%2B%2B", "/discover?q=C%2B%2B"],
  ["login", "//evil.example", "/profile"],
  ["link", "/ignored", "/applications?q=R%26D"],
] as const)(
  "successful %s completion redirects safely from %s",
  async (purpose, next, expected) => {
    const assign = vi.fn();
    const originalWindow = window;
    vi.stubGlobal(
      "window",
      new Proxy(originalWindow, {
        get(target, key) {
          if (key === "location")
            return { pathname: "/applications", search: "?q=R%26D", assign };
          return Reflect.get(target, key);
        },
      }),
    );
    transport
      .mockResolvedValueOnce(challenge)
      .mockResolvedValueOnce({ status: "approved" })
      .mockResolvedValueOnce({ ok: true });
    render(<TelegramAuth purpose={purpose} next={next} />);
    if (purpose === "link") {
      fireEvent.change(screen.getByLabelText("Подтвердите пароль аккаунта"), {
        target: { value: "password confirmation" },
      });
      fireEvent.submit(screen.getByRole("button").closest("form")!);
    } else fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(expected));
    expect(assign).toHaveBeenCalledTimes(1);
    expect(transport.mock.calls.map((call) => call[0])).toEqual([
      "/api/auth/telegram/challenges",
      "/api/auth/telegram/status",
      "/api/auth/telegram/complete",
    ]);
  },
);

it("creation failure closes reserved tab without navigating to Telegram", async () => {
  transport.mockRejectedValueOnce(new WebError("auth_unavailable", 503));
  render(<TelegramAuth purpose="login" />);
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  expect(popup.close).toHaveBeenCalledTimes(1);
  expect(popup.location.replace).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("link", { name: "Открыть Telegram" }),
  ).not.toBeInTheDocument();
});
it.each(["blocked", "throws", "closed", "navigation-error"])(
  "%s automatic open keeps valid challenge and fallback",
  async (failure) => {
    if (failure === "blocked") vi.mocked(window.open).mockReturnValueOnce(null);
    if (failure === "throws")
      vi.mocked(window.open).mockImplementationOnce(() => {
        throw new Error("blocked");
      });
    if (failure === "closed") popup.closed = true;
    if (failure === "navigation-error")
      popup.location.replace.mockImplementationOnce(() => {
        throw new Error("navigation");
      });
    transport
      .mockResolvedValueOnce(challenge)
      .mockImplementationOnce(() => new Promise(() => {}));
    render(<TelegramAuth purpose="login" />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() =>
      expect(
        screen.getByRole("link", { name: "Открыть Telegram" }),
      ).toHaveAttribute("href", challenge.deep_link),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Ждём подтверждения");
    expect(transport.mock.calls[0][0]).toBe("/api/auth/telegram/challenges");
  },
);
it.each(["expired", "cancelled"])(
  "%s permits a fresh challenge and automatic open on retry",
  async (status) => {
    transport
      .mockResolvedValueOnce(challenge)
      .mockResolvedValueOnce({ status })
      .mockResolvedValueOnce(challenge)
      .mockImplementationOnce(() => new Promise(() => {}));
    render(<TelegramAuth purpose="login" />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Попробовать снова" }),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    await waitFor(() =>
      expect(
        screen.getByRole("link", { name: "Открыть Telegram" }),
      ).toBeInTheDocument(),
    );
    expect(
      transport.mock.calls.filter((call) => call[0].endsWith("challenges")),
    ).toHaveLength(2);
    expect(window.open).toHaveBeenCalledTimes(2);
  },
);
it("linking retains manual open behavior", async () => {
  transport
    .mockResolvedValueOnce(challenge)
    .mockImplementationOnce(() => new Promise(() => {}));
  render(<TelegramAuth purpose="link" />);
  fireEvent.change(screen.getByLabelText("Подтвердите пароль аккаунта"), {
    target: { value: "confirm password" },
  });
  fireEvent.submit(screen.getByRole("button").closest("form")!);
  await waitFor(() =>
    expect(
      screen.getByRole("link", { name: "Открыть Telegram" }),
    ).toBeInTheDocument(),
  );
  expect(window.open).not.toHaveBeenCalled();
});

it.each(["success", "outage"])(
  "Web cancel %s stops an in-flight poll, restores focus and allows fresh login",
  async (result) => {
    vi.useFakeTimers();
    let resolveStatus!: (value: unknown) => void;
    let resolveCancel!: (value: unknown) => void;
    let rejectCancel!: (reason: unknown) => void;
    transport.mockImplementation((url: string) => {
      if (url.endsWith("challenges")) return Promise.resolve(challenge);
      if (url.endsWith("status"))
        return new Promise((resolve) => {
          resolveStatus = resolve;
        });
      if (url.endsWith("cancel"))
        return new Promise((resolve, reject) => {
          resolveCancel = resolve;
          rejectCancel = reject;
        });
      throw new Error("Completion must not start after local cancel");
    });
    render(<TelegramAuth purpose="login" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const pollSignal = transport.mock.calls.find((call) =>
      call[0].endsWith("status"),
    )![1].signal;
    const cancel = screen.getByRole("button", { name: "Отменить вход" });
    await act(async () => {
      fireEvent.click(cancel);
      fireEvent.click(cancel);
    });
    expect(
      screen.queryByText("Ждём подтверждения в Telegram…"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Открыть Telegram" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Вход отменён");
    expect(
      screen.getByRole("button", { name: "Попробовать снова" }),
    ).toHaveFocus();
    expect(pollSignal.aborted).toBe(true);
    expect(
      transport.mock.calls.filter((call) => call[0].endsWith("cancel")),
    ).toHaveLength(1);
    expect(
      JSON.parse(
        transport.mock.calls.find((call) => call[0].endsWith("cancel"))![1]
          .body,
      ),
    ).toEqual({ purpose: "login", token: challenge.token });
    // Start immediately; creation waits only for the old cookie-clearing response.
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    expect(
      transport.mock.calls.filter((call) => call[0].endsWith("challenges")),
    ).toHaveLength(1);
    await act(async () => {
      resolveStatus({ status: "approved" });
      if (result === "success") resolveCancel({ ok: true });
      else rejectCancel(new WebError("auth_unavailable", 503));
    });
    expect(
      transport.mock.calls.some((call) => call[0].endsWith("complete")),
    ).toBe(false);
    expect(
      transport.mock.calls.filter((call) => call[0].endsWith("challenges")),
    ).toHaveLength(2);
  },
);
it("cancel with API outage returns to normal login UI without alert or background polling", async () => {
  vi.useFakeTimers();
  transport.mockImplementation((url: string) => {
    if (url.endsWith("challenges")) return Promise.resolve(challenge);
    if (url.endsWith("cancel"))
      return Promise.reject(new WebError("auth_unavailable", 503));
    return Promise.resolve({ status: "pending" });
  });
  render(<TelegramAuth purpose="login" />);
  await act(async () => {
    fireEvent.click(screen.getByRole("button"));
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Отменить вход" }));
  });
  const count = transport.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(transport).toHaveBeenCalledTimes(count);
  expect(
    screen.getByRole("button", { name: "Попробовать снова" }),
  ).toBeEnabled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
it("unmount aborts in-flight status and prevents late approval completion", async () => {
  vi.useFakeTimers();
  let resolve!: (value: unknown) => void;
  transport.mockResolvedValueOnce(challenge).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const view = render(<TelegramAuth purpose="login" />);
  await act(async () => {
    fireEvent.click(screen.getByRole("button"));
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  const signal = transport.mock.calls[1][1].signal;
  view.unmount();
  await act(async () => {
    resolve({ status: "approved" });
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(signal.aborted).toBe(true);
  expect(transport).toHaveBeenCalledTimes(2);
});
it.each(["expired", "cancelled", "conflict", "error"])(
  "terminal %s leaves no polling timers",
  async (status) => {
    vi.useFakeTimers();
    transport.mockResolvedValueOnce(challenge);
    if (status === "error")
      transport.mockRejectedValueOnce(new WebError("auth_unavailable", 503));
    else transport.mockResolvedValueOnce({ status });
    render(<TelegramAuth purpose="login" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(transport).toHaveBeenCalledTimes(2);
    expect(
      screen.queryByRole("button", { name: "Отменить вход" }),
    ).not.toBeInTheDocument();
  },
);
it("completion disables cancel and successful login stops polling", async () => {
  vi.useFakeTimers();
  let resolve!: (value: unknown) => void;
  const assign = vi.fn();
  const originalWindow = window;
  vi.stubGlobal(
    "window",
    new Proxy(originalWindow, {
      get(target, key) {
        if (key === "location")
          return { pathname: "/login", search: "", assign };
        return Reflect.get(target, key);
      },
    }),
  );
  transport
    .mockResolvedValueOnce(challenge)
    .mockResolvedValueOnce({ status: "approved" })
    .mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
  render(<TelegramAuth purpose="login" />);
  await act(async () => {
    fireEvent.click(screen.getByRole("button"));
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  const cancel = screen.getByRole("button", { name: "Отменить вход" });
  expect(cancel).toBeDisabled();
  fireEvent.click(cancel);
  await act(async () => {
    resolve({ ok: true });
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(assign).toHaveBeenCalledWith("/profile");
  expect(transport).toHaveBeenCalledTimes(3);
  expect(transport.mock.calls[2][1].signal.aborted).toBe(true);
});
