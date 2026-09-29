import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Discover } from "../components/discover";
import { ApplicationView } from "../components/application-detail";
import { Matching } from "../components/vacancy";
import { page, saved, vacancy } from "./fixtures";
const response = (value: unknown, status = 200) => ({
  ok: status < 400,
  status,
  json: async () => value,
});
afterEach(() => vi.unstubAllGlobals());
function submit(query = "Python") {
  fireEvent.change(screen.getByLabelText("Должность или ключевые слова"), {
    target: { value: query },
  });
  fireEvent.click(screen.getByRole("button", { name: "Найти вакансии" }));
}
async function select() {
  fireEvent.click(
    await screen.findByRole("button", {
      name: /Python developer.*Test company/,
    }),
  );
}
describe("Discover", () => {
  it("searches only on submit; renders source text safely and unavailable matching", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(page));
    vi.stubGlobal("fetch", fetcher);
    render(<Discover />);
    fireEvent.change(screen.getByLabelText("Должность или ключевые слова"), {
      target: { value: "Python" },
    });
    expect(fetcher).not.toHaveBeenCalled();
    submit();
    await select();
    expect(screen.getByText(vacancy.description!)).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText(/Для оценки нужен профиль/)).toBeInTheDocument();
    expect(screen.queryByText(/0\/100/)).not.toBeInTheDocument();
  });
  it("omits the detail panel until a result is selected, then shows it", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(page)));
    render(<Discover />);
    submit();
    await screen.findByRole("heading", { name: "Результаты поиска" });
    const grid = document.querySelector(".discover-grid");
    expect(grid).not.toHaveClass("has-selection");
    expect(
      screen.queryByLabelText("Описание вакансии"),
    ).not.toBeInTheDocument();
    await select();
    expect(screen.getByLabelText("Описание вакансии")).toBeInTheDocument();
    expect(grid).toHaveClass("has-selection");
    expect(
      screen.getByRole("navigation", { name: "Страницы результатов" }),
    ).toBeInTheDocument();
  });
  it("shows a source description preview before selection and removes it in split view", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(page)));
    render(<Discover />);
    submit();

    const preview = await screen.findByText(vacancy.description!, {
      selector: ".card-description",
    });
    expect(preview).toBeInTheDocument();
    await select();
    expect(
      screen.queryByText(vacancy.description!, {
        selector: ".card-description",
      }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Описание вакансии")).toHaveTextContent(
      vacancy.description!,
    );
  });
  it("keeps the card name concise when the source description is long", async () => {
    const description = "Подробное описание вакансии. ".repeat(80);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({
          ...page,
          items: [{ ...vacancy, description }],
        }),
      ),
    );
    render(<Discover />);
    submit();
    const card = await screen.findByRole("button", {
      name: "Python developer — Test company — Москва",
    });
    expect(card).toHaveAccessibleName(
      "Python developer — Test company — Москва",
    );
    expect(card).toHaveTextContent(description);
    fireEvent.click(card);
    expect(screen.getByLabelText("Описание вакансии")).toHaveTextContent(
      description,
    );
  });
  it("distinguishes cards with the same title and company without reading their descriptions", async () => {
    const description = "Длинное описание. ".repeat(80);
    const first = { ...vacancy, location: "Москва", description };
    const second = {
      ...vacancy,
      external_id: "job-2",
      location: "Санкт-Петербург",
      description,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response({ ...page, items: [first, second] })),
    );
    render(<Discover />);
    submit();
    const firstCard = await screen.findByRole("button", {
      name: "Python developer — Test company — Москва",
    });
    const secondCard = screen.getByRole("button", {
      name: "Python developer — Test company — Санкт-Петербург",
    });
    expect(firstCard).toHaveAccessibleName(
      "Python developer — Test company — Москва",
    );
    expect(secondCard).toHaveAccessibleName(
      "Python developer — Test company — Санкт-Петербург",
    );
    expect(firstCard).toHaveTextContent(description);
    expect(secondCard).toHaveTextContent(description);
  });
  it("uses result position only when card metadata is otherwise identical", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({
          ...page,
          items: [vacancy, { ...vacancy, external_id: "job-2" }],
        }),
      ),
    );
    render(<Discover />);
    submit();
    expect(
      await screen.findByRole("button", {
        name: "Python developer — Test company — Москва — результат 1",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Python developer — Test company — Москва — результат 2",
      }),
    ).toBeInTheDocument();
  });
  it.each([null, "", "   "])(
    "omits the description preview when source description is %s",
    async (description) => {
      const pageWithoutDescription = {
        ...page,
        items: [{ ...vacancy, description }],
      };
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(response(pageWithoutDescription)),
      );
      render(<Discover />);
      submit();
      await screen.findByRole("button", { name: /Python developer/ });
      expect(
        document.querySelector(".card-description"),
      ).not.toBeInTheDocument();
    },
  );
  it("sends identity only and links the returned reused Application without resetting its status", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(response(page))
      .mockResolvedValueOnce(response(saved));
    vi.stubGlobal("fetch", fetcher);
    render(<Discover />);
    submit();
    await select();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить вакансию" }));
    expect(
      await screen.findByRole("link", { name: /Открыть сохранённую/ }),
    ).toHaveAttribute("href", "/applications/42");
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
      source: vacancy.source,
      source_scope: vacancy.source_scope,
      external_id: vacancy.external_id,
    });
    expect(screen.getByText(/уже была в ваших откликах/)).toBeInTheDocument();
    expect(
      screen.getByText(/Текущий статус: Собеседование/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Вакансия сохранена")).not.toBeInTheDocument();
  });
  it("confirms a newly created Application and prevents duplicate in-flight saves", async () => {
    let finish!: (value: unknown) => void;
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(response(page))
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
    vi.stubGlobal("fetch", fetcher);
    render(<Discover />);
    submit();
    await select();
    const button = screen.getByRole("button", { name: "Сохранить вакансию" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(button).toBeDisabled();
    await act(async () =>
      finish(
        response({
          ...saved,
          application_created: true,
          application: { ...saved.application, status: "saved" },
        }),
      ),
    );
    expect(await screen.findByText("Вакансия сохранена")).toBeInTheDocument();
  });
  it("does not overwrite newer search results even when old fetch ignores cancellation", async () => {
    let finishOld!: (value: unknown) => void;
    const fetcher = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishOld = resolve;
          }),
      )
      .mockResolvedValueOnce(
        response({ ...page, items: [{ ...vacancy, title: "New result" }] }),
      );
    vi.stubGlobal("fetch", fetcher);
    render(<Discover />);
    submit("Old");
    submit("New");
    await screen.findByText("New result");
    await act(async () => finishOld(response(page)));
    expect(screen.getByText("New result")).toBeInTheDocument();
    expect(screen.queryByText("Python developer")).not.toBeInTheDocument();
  });
  it("allows next_offset pagination on an empty filtered page", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      response({
        ...page,
        items: [],
        returned_count: 0,
        next_offset: 10,
        locally_filtered: true,
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    render(<Discover />);
    submit();
    fireEvent.click(await screen.findByRole("button", { name: "Далее →" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(fetcher.mock.calls[1][0]).toContain("offset=10");
  });
  it("never saves an already-saved search item just to resolve its ID", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      response({
        ...page,
        items: [{ ...vacancy, already_saved_for_user: true }],
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    render(<Discover />);
    submit();
    await select();
    expect(screen.getByText("Уже в моих вакансиях")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Сохранить вакансию" }),
    ).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("reports uncertain network save without claiming rollback", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(response(page))
        .mockRejectedValueOnce(new TypeError("network")),
    );
    render(<Discover />);
    submit();
    await select();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить вакансию" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Запись могла быть создана",
    );
  });
});
it("renders insufficient data without inventing a score", () => {
  render(
    <Matching
      preview={{
        ...vacancy.preview_match,
        available: true,
        verdict: "insufficient_data",
        coverage: 20,
      }}
    />,
  );
  expect(screen.getByText("Недостаточно данных")).toBeInTheDocument();
  expect(screen.queryByText(/\/100/)).not.toBeInTheDocument();
});
it("renders read-only Application notes, next action, actual status and source", () => {
  render(<ApplicationView detail={saved} />);
  expect(
    screen.getByRole("heading", { name: "Python developer" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Собеседование")).toBeInTheDocument();
  expect(screen.getByText("Уточнить условия")).toBeInTheDocument();
  expect(screen.getByText("2026-10-01")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: /Открыть у источника/ }),
  ).toHaveAttribute("href", vacancy.source_url);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
it("disables duplicate pending search but allows a new submitted query", async () => {
  let finish!: (value: unknown) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    ),
  );
  render(<Discover />);
  submit();
  expect(screen.getByRole("button", { name: "Найти вакансии" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Должность или ключевые слова"), {
    target: { value: "Another" },
  });
  expect(screen.getByRole("button", { name: "Найти вакансии" })).toBeEnabled();
  await act(async () => finish(response(page)));
});
it("returns from compact detail to the selected result and restores keyboard focus", async () => {
  vi.stubGlobal("innerWidth", 768);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 0;
  });
  const originalScroll = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "scrollIntoView",
  );
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(page)));
  render(<Discover />);
  submit();
  await select();
  const card = screen.getByRole("button", {
    name: /Python developer.*Test company/,
  });
  expect(card).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByLabelText("Выбрана")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "← К результатам" }));
  expect(card).toHaveFocus();
  expect(
    screen.queryByRole("button", { name: "Сохранить вакансию" }),
  ).not.toBeInTheDocument();
  if (originalScroll)
    Object.defineProperty(
      HTMLElement.prototype,
      "scrollIntoView",
      originalScroll,
    );
  else Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
});
