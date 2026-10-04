import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CVImport } from "../components/cv-import";
import { cvPreview } from "./cv-import-fixture";

afterEach(() => vi.unstubAllGlobals());
async function open() {
  render(<CVImport />);
  fireEvent.change(screen.getByLabelText("Файл резюме"), {
    target: {
      files: [new File(["pdf"], "resume.pdf", { type: "application/pdf" })],
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "Распознать резюме" }));
  await screen.findByRole("heading", { name: "Проверьте данные" });
}
it("local work edit disables Apply, Cancel restores read mode without saving", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(cvPreview));
  vi.stubGlobal("fetch", fetcher);
  await open();
  fireEvent.click(
    screen.getByRole("button", { name: "Изменить опыт работы 1" }),
  );
  expect(screen.getByLabelText("Должность")).toHaveFocus();
  expect(screen.getByRole("group", { name: "Действия с резюме" })).toHaveClass(
    "has-editor",
  );
  fireEvent.change(screen.getByLabelText("Должность"), {
    target: { value: "Local change" },
  });
  expect(
    screen.getByRole("button", { name: "Применить к профилю" }),
  ).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent(
    "Сохраните или отмените",
  );
  expect(
    screen.getByRole("form", { name: "Редактирование предпросмотра" }),
  ).toHaveClass("cv-preview-editor");
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(
    screen.getByRole("group", { name: "Действия с резюме" }),
  ).not.toHaveClass("has-editor");
  expect(
    screen.getByRole("button", { name: "Изменить опыт работы 1" }),
  ).toHaveFocus();
  expect(
    screen.getByRole("button", { name: "Применить к профилю" }),
  ).toBeEnabled();
  expect(screen.getByText("Engineer")).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it("Save sends typed edit with revision, refreshed result becomes read-only server proposal", async () => {
  const changed = {
    ...cvPreview,
    revision: 2,
    work_experience: [
      {
        ...cvPreview.work_experience[0],
        company: "Corrected project",
        position: "Corrected role",
      },
    ],
  };
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(cvPreview))
    .mockResolvedValueOnce(Response.json(changed));
  vi.stubGlobal("fetch", fetcher);
  await open();
  fireEvent.click(
    screen.getByRole("button", { name: "Изменить опыт работы 1" }),
  );
  fireEvent.change(screen.getByLabelText("Должность"), {
    target: { value: "Corrected role" },
  });
  fireEvent.change(screen.getByLabelText("Компания / проект"), {
    target: { value: "Corrected project" },
  });
  expect(screen.getByLabelText("Окончание: год")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Corrected project");
  expect(
    screen.getByRole("group", { name: "Действия с резюме" }),
  ).not.toHaveClass("has-editor");
  expect(fetcher.mock.calls[1][0]).toBe("/api/profile/import/preview");
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
    token: cvPreview.token,
    revision: 1,
    edit: { kind: "work", index: 0, value: changed.work_experience[0] },
  });
  expect(
    screen.getByRole("button", { name: "Применить к профилю" }),
  ).toBeEnabled();
});
it.each([
  "Изменить факт 1",
  "Изменить языки",
  "Изменить навыки",
  "Изменить основные данные",
])("%s puts actions in editor mode until Cancel", async (name) => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(cvPreview));
  vi.stubGlobal("fetch", fetcher);
  await open();
  const trigger = screen.getByRole("button", { name });
  fireEvent.click(trigger);
  expect(screen.getByRole("group", { name: "Действия с резюме" })).toHaveClass(
    "has-editor",
  );
  expect(
    screen.getByRole("button", { name: "Применить к профилю" }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(screen.getByRole("button", { name })).toHaveFocus();
  expect(
    screen.getByRole("group", { name: "Действия с резюме" }),
  ).not.toHaveClass("has-editor");
  expect(
    screen.getByRole("button", { name: "Применить к профилю" }),
  ).toBeEnabled();
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it("delete changes trusted snapshot and summary, not local-only rendering", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(cvPreview))
    .mockResolvedValueOnce(
      Response.json({ ...cvPreview, revision: 2, work_experience: [] }),
    );
  vi.stubGlobal("fetch", fetcher);
  await open();
  fireEvent.click(
    screen.getByRole("button", { name: "Удалить опыт работы 1" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("list", { name: "После применения" }),
    ).toHaveTextContent("Опыт работы: 3 → 0"),
  );
  expect(JSON.parse(fetcher.mock.calls[1][1].body).edit).toEqual({
    kind: "delete_work",
    index: 0,
  });
  expect(screen.getByRole("region", { name: "Важно" })).toHaveTextContent(
    "текущий опыт работы будет удалён",
  );
});
it("validation errors stay in editor near dates and Apply stays disabled", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(Response.json(cvPreview))
      .mockResolvedValueOnce(
        Response.json(
          {
            code: "cv_import_edit_invalid",
            fieldErrors: { dates: "Проверьте даты." },
          },
          { status: 422 },
        ),
      ),
  );
  await open();
  fireEvent.click(
    screen.getByRole("button", { name: "Изменить опыт работы 1" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Проверьте даты.");
  expect(screen.getByLabelText("Начало: год")).toHaveAttribute(
    "aria-describedby",
    "cv-error-dates",
  );
  expect(
    screen.getByRole("button", { name: "Применить к профилю" }),
  ).toBeDisabled();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
});
it("facts support Save and delete with new revision and refreshed counts", async () => {
  const changed = {
    ...cvPreview,
    revision: 2,
    experience_facts: ["Corrected fact"],
  };
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(cvPreview))
    .mockResolvedValueOnce(Response.json(changed))
    .mockResolvedValueOnce(
      Response.json({ ...changed, revision: 3, experience_facts: [] }),
    );
  vi.stubGlobal("fetch", fetcher);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Изменить факт 1" }));
  expect(
    screen.getByRole("textbox", { name: "Факт практического опыта" }),
  ).toHaveFocus();
  fireEvent.change(screen.getByLabelText("Факт практического опыта"), {
    target: { value: "Corrected fact" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Corrected fact");
  fireEvent.click(screen.getByRole("button", { name: "Удалить факт 1" }));
  await waitFor(() =>
    expect(
      screen.getByRole("list", { name: "После применения" }),
    ).toHaveTextContent("Практический опыт: 4 → 0"),
  );
  expect(JSON.parse(fetcher.mock.calls[2][1].body).revision).toBe(2);
});
it("skills token editor adds on Enter and sends allowed profile projection", async () => {
  const changed = {
    ...cvPreview,
    revision: 2,
    proposed: {
      ...cvPreview.proposed,
      skills: [...cvPreview.proposed.skills, "Docker"],
    },
  };
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(cvPreview))
    .mockResolvedValueOnce(Response.json(changed));
  vi.stubGlobal("fetch", fetcher);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Изменить навыки" }));
  fireEvent.change(screen.getByLabelText("Добавить: Навыки"), {
    target: { value: "Docker" },
  });
  fireEvent.keyDown(screen.getByLabelText("Добавить: Навыки"), {
    key: "Enter",
  });
  expect(
    within(
      screen.getByRole("form", { name: "Редактирование предпросмотра" }),
    ).getByText("Docker"),
  ).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Docker");
  expect(JSON.parse(fetcher.mock.calls[1][1].body).edit).toEqual({
    kind: "profile",
    value: changed.proposed,
  });
});
