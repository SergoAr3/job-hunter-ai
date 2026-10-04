import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CVImport } from "../components/cv-import";
import { CV_MAX_BYTES } from "../lib/cv-import";
import { cvPreview } from "./cv-import-fixture";

afterEach(() => vi.unstubAllGlobals());
it.each([
  { currency: "USD", period: "month", same: true },
  { currency: "EUR", period: "month", same: false },
  { currency: "USD", period: "year", same: false },
] as const)(
  "compares the complete salary block with decimal formatting ignored: %j",
  async ({ currency, period, same }) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json({
          ...cvPreview,
          current: {
            ...cvPreview.current,
            salary_min: "3000.00",
            salary_currency: "USD",
            salary_period: "month",
          },
          proposed: {
            ...cvPreview.proposed,
            salary_min: "3000",
            salary_currency: currency,
            salary_period: period,
          },
        }),
      ),
    );
    render(<CVImport />);
    choose();
    fireEvent.click(screen.getByRole("button", { name: "Распознать резюме" }));
    const salary = (await screen.findByText("Зарплата от")).parentElement!;
    if (same) {
      expect(within(salary).getByText("без изменений")).toBeInTheDocument();
      expect(within(salary).queryByText("Сейчас")).not.toBeInTheDocument();
    } else {
      expect(within(salary).getByText("Сейчас")).toBeInTheDocument();
      expect(within(salary).getByText("После применения")).toBeInTheDocument();
    }
  },
);
function choose(
  file = new File(["document"], "resume.pdf", { type: "application/pdf" }),
) {
  fireEvent.change(screen.getByLabelText("Файл резюме"), {
    target: { files: [file] },
  });
}
function redirectMock() {
  const assign = vi.fn();
  const original = window;
  vi.stubGlobal(
    "window",
    new Proxy(original, {
      get(target, key) {
        return key === "location" ? { assign } : Reflect.get(target, key);
      },
    }),
  );
  return assign;
}
it.each([false, true])(
  "shows replacement warning and trusted counts with unchanged main fields (empty=%s)",
  async (empty) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json({
          ...cvPreview,
          proposed: {
            ...cvPreview.current,
            skills: Array.from({ length: 28 }, (_, i) => `Skill ${i + 1}`),
          },
          current_work_experience_count: 8,
          current_experience_fact_count: 16,
          work_experience: empty
            ? []
            : Array.from({ length: 5 }, () => cvPreview.work_experience[0]),
          experience_facts: empty
            ? []
            : Array.from({ length: 8 }, (_, i) => `Fact ${i + 1}`),
        }),
      ),
    );
    render(<CVImport />);
    choose();
    fireEvent.click(screen.getByRole("button", { name: "Распознать резюме" }));
    const warning = await screen.findByRole("region", { name: "Важно" });
    expect(warning).toHaveTextContent(
      "будут полностью заменены данными из этого резюме",
    );
    expect(warning).toHaveTextContent(
      "Текущие записи, которых нет в новом резюме, будут удалены",
    );
    expect(warning).toHaveTextContent(
      "вручную добавленных записей опыта работы и практического опыта",
    );
    expect(warning).not.toHaveAttribute("role", "alert");
    const summary = screen.getByRole("list", { name: "После применения" });
    expect(summary).toHaveClass("cv-preview-summary");
    expect(summary).toHaveTextContent("Основные данные: без изменений");
    expect(summary).toHaveTextContent("Навыки: 28 после применения");
    expect(summary).toHaveTextContent(`Опыт работы: 8 → ${empty ? 0 : 5}`);
    expect(summary).toHaveTextContent(
      `Практический опыт: 16 → ${empty ? 0 : 8}`,
    );
    expect(
      screen.queryByText(/изменённых полей профиля/),
    ).not.toBeInTheDocument();
    if (empty) {
      expect(warning).toHaveTextContent(
        "текущий опыт работы будет удалён, потому что в резюме не найдено записей опыта",
      );
      expect(warning).toHaveTextContent(
        "текущий практический опыт будет удалён, потому что в резюме не найдено фактов",
      );
    }
    expect(
      warning.compareDocumentPosition(
        screen.getByRole("heading", { name: "Профиль" }),
      ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  },
);
it("upload processing and preview show actual current/proposed; no mutation before Apply", async () => {
  let resolve!: (response: Response) => void;
  const fetcher = vi.fn<typeof fetch>(
    () =>
      new Promise<Response>((done) => {
        resolve = done;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<CVImport />);
  expect(screen.getByText(/Ничего не изменится/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Распознать резюме" }),
  ).toBeDisabled();
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Распознать резюме" }));
  expect(screen.getByRole("status")).toHaveTextContent("Обрабатываем резюме…");
  await act(async () => resolve(Response.json(cvPreview)));
  expect(
    screen.getByRole("heading", { name: "Проверьте данные" }),
  ).toHaveFocus();
  expect(screen.getByText("Developer")).toBeInTheDocument();
  expect(screen.getByText("Developer, Engineer")).toBeInTheDocument();
  expect(screen.getByText("Acme")).toBeInTheDocument();
  expect(
    screen.getByRole("list", { name: "После применения" }),
  ).toHaveTextContent("Основные данные: 2 изменения");
  const workSection = screen.getByRole("heading", {
    name: "Опыт работы",
  }).parentElement!;
  expect(
    within(workSection).getByText(/текущий опыт работы будет заменён/),
  ).toBeInTheDocument();
  expect(
    within(workSection).getByText("Сейчас: 3 · После применения: 1"),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(/Существующие записи сохраняются/),
  ).not.toBeInTheDocument();
  const unchangedLocation = screen.getByText("Локации").parentElement!;
  expect(
    within(unchangedLocation).getByText("без изменений"),
  ).toBeInTheDocument();
  expect(within(unchangedLocation).getAllByText("Yerevan")).toHaveLength(1);
  expect(
    within(unchangedLocation).queryByText("Сейчас"),
  ).not.toBeInTheDocument();
  const roles = screen.getByText("Желаемые роли").parentElement!;
  expect(within(roles).getByText("Сейчас")).toBeInTheDocument();
  expect(within(roles).getByText("После применения")).toBeInTheDocument();
  expect(screen.getByText("Python").closest("li")).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Навыки и языки" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Практический опыт" }),
  ).toBeInTheDocument();
  const factsSection = screen.getByRole("heading", {
    name: "Практический опыт",
  }).parentElement!;
  expect(
    within(factsSection).getByText(
      /весь текущий практический опыт будет заменён/,
    ),
  ).toBeInTheDocument();
  expect(
    within(factsSection).getByText("Сейчас: 4 · После применения: 1"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("group", { name: "Действия с резюме" }),
  ).toBeInTheDocument();
  expect(screen.getByText("2020 — по настоящее время")).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe("/api/profile/import");
});
it("warns that applying an empty work snapshot clears all current work", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({
        ...cvPreview,
        work_experience: [],
        experience_facts: [],
      }),
    ),
  );
  render(<CVImport />);
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Распознать резюме" }));
  await screen.findByText("Проверьте данные");
  expect(
    screen.getByText("Сейчас: 4 · После применения: 0"),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/текущий список фактов будет пустым/),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Сейчас: 3 · После применения: 0"),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/текущий список опыта работы будет пустым/),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Применить к профилю" }),
  ).toBeEnabled();
});
it("replaces the main upload zone with one selected file and keeps the native change picker", () => {
  const { container } = render(<CVImport />);
  const zone = screen.getByText("Перетащите PDF или DOCX сюда").parentElement!;
  expect(within(zone).getByText("Загрузите резюме")).toBeInTheDocument();
  const input = screen.getByLabelText("Файл резюме");
  const click = vi.spyOn(input, "click");
  fireEvent.click(screen.getByRole("button", { name: "Выбрать файл" }));
  expect(click).toHaveBeenCalledOnce();
  expect(input).toHaveAttribute("type", "file");
  choose(
    new File([new Uint8Array(1258291)], "CV_Harutyunyan.pdf", {
      type: "application/pdf",
    }),
  );
  expect(screen.getByRole("status")).toHaveTextContent("CV_Harutyunyan.pdf");
  expect(screen.getByRole("status")).toHaveTextContent("1.2 МБ");
  expect(within(zone).getByText("CV_Harutyunyan.pdf")).toHaveAttribute(
    "title",
    "CV_Harutyunyan.pdf",
  );
  expect(
    within(zone).getByText("Файл готов к распознаванию"),
  ).toBeInTheDocument();
  expect(screen.getAllByText("CV_Harutyunyan.pdf")).toHaveLength(1);
  expect(container.querySelector(".cv-file-summary")).toBeNull();
  expect(
    screen.queryByText("Перетащите PDF или DOCX сюда"),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Выбрать другой файл" }));
  expect(click).toHaveBeenCalledTimes(2);
  choose(
    new File([new Uint8Array(56699)], "replacement.pdf", {
      type: "application/pdf",
    }),
  );
  expect(within(zone).getByText("replacement.pdf")).toBeInTheDocument();
  expect(within(zone).getByText("56 КБ")).toBeInTheDocument();
  expect(screen.queryByText("CV_Harutyunyan.pdf")).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Распознать резюме" }),
  ).toBeEnabled();
});
it("drop selects the same file for multipart upload and keeps local error feedback", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(cvPreview));
  vi.stubGlobal("fetch", fetcher);
  render(<CVImport />);
  const zone = screen.getByText("Перетащите PDF или DOCX сюда").parentElement!;
  fireEvent.drop(zone, {
    dataTransfer: { files: [new File(["bad"], "bad.txt")] },
  });
  expect(screen.getByRole("alert")).not.toBeEmptyDOMElement();
  expect(fetcher).not.toHaveBeenCalled();
  const file = new File(["document"], "dropped.pdf", {
    type: "application/pdf",
  });
  fireEvent.drop(zone, { dataTransfer: { files: [file] } });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Распознать резюме" }));
  await screen.findByText("Проверьте данные");
  expect(fetcher.mock.calls[0][1].body.get("file").name).toBe("dropped.pdf");
});
it.each(["apply", "cancel"] as const)(
  "explicit %s sends token only and redirects safely",
  async (kind) => {
    const assign = redirectMock();
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json(cvPreview))
      .mockResolvedValue(Response.json({ ok: true }));
    vi.stubGlobal("fetch", fetcher);
    render(<CVImport />);
    choose();
    fireEvent.click(screen.getByRole("button", { name: "Распознать резюме" }));
    const action = await screen.findByRole("button", {
      name: kind === "apply" ? "Применить к профилю" : "Отменить",
    });
    fireEvent.click(action);
    fireEvent.click(action);
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith(
        kind === "apply" ? "/profile?imported=1" : "/profile",
      ),
    );
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1][0]).toBe(`/api/profile/import/${kind}`);
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
      token: cvPreview.token,
      revision: cvPreview.revision,
    });
  },
);
it.each([
  "ai_timeout",
  "no_extractable_text",
  "cv_import_unavailable",
  "auth_unavailable",
  "cv_import_busy",
])("extraction error %s stays error, never preview", async (code) => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ code }, { status: 503 })),
  );
  render(<CVImport />);
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Распознать резюме" }));
  expect(await screen.findByRole("alert")).not.toBeEmptyDOMElement();
  if (code === "cv_import_busy") {
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Подождите немного и попробуйте снова",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent(/отмен|активных/i);
  }
  expect(
    screen.queryByRole("button", { name: "Применить к профилю" }),
  ).not.toBeInTheDocument();
});
it.each([
  new File(["text"], "resume.txt"),
  new File([], "resume.pdf"),
  new File([new Uint8Array(CV_MAX_BYTES + 1)], "resume.docx"),
])("invalid type/empty/size stays local", (file) => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(<CVImport />);
  choose(file);
  expect(screen.getByRole("alert")).not.toBeEmptyDOMElement();
  expect(
    screen.getByRole("button", { name: "Распознать резюме" }),
  ).toBeDisabled();
  expect(fetcher).not.toHaveBeenCalled();
});
it("cancel processing aborts transport and ignores late extraction response", async () => {
  let resolve!: (response: Response) => void;
  const fetcher = vi.fn<typeof fetch>(
    () =>
      new Promise<Response>((done) => {
        resolve = done;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<CVImport />);
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Распознать резюме" }));
  fireEvent.click(screen.getByRole("button", { name: "Отменить обработку" }));
  expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
  await act(async () => resolve(Response.json(cvPreview)));
  expect(screen.getByLabelText("Файл резюме")).toBeInTheDocument();
  expect(screen.queryByText("Проверьте данные")).not.toBeInTheDocument();
});
it("uncertain Apply tells user to inspect profile", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(Response.json(cvPreview))
      .mockRejectedValue(new Error("network")),
  );
  render(<CVImport />);
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Распознать резюме" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Применить к профилю" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Проверьте профиль перед новым импортом",
  );
});
it("failed extraction clears the selection before retrying with another file", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({ code: "malformed_document" }, { status: 422 }),
    )
    .mockResolvedValueOnce(Response.json(cvPreview));
  vi.stubGlobal("fetch", fetcher);
  render(<CVImport />);
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Распознать резюме" }));
  await screen.findByRole("alert");
  expect(
    screen.getByRole("button", { name: "Распознать резюме" }),
  ).toBeDisabled();
  choose(new File(["docx"], "replacement.docx"));
  fireEvent.click(screen.getByRole("button", { name: "Распознать резюме" }));
  await screen.findByText("Проверьте данные");
  expect(fetcher).toHaveBeenCalledTimes(2);
});
