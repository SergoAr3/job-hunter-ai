import { expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { CVPreviewEditor } from "../components/cv-preview-editor";
import { cvPreview } from "./cv-import-fixture";

function choose(label: string, option: string) {
  fireEvent.click(
    screen.getByRole("combobox", { name: new RegExp(`^${label} `) }),
  );
  fireEvent.click(screen.getByRole("option", { name: option }));
}
it("CV profile enums retain canonical values and associate field errors", () => {
  const save = vi.fn();
  render(
    <CVPreviewEditor
      preview={cvPreview}
      target={{ kind: "profile", field: "main" }}
      pending={false}
      errors={{ experience: "Check experience" }}
      message=""
      onSave={save}
      onCancel={vi.fn()}
    />,
  );
  expect(
    screen.getByRole("combobox", { name: /^Уровень опыта / }),
  ).toHaveAccessibleDescription("Check experience");
  choose("Уровень опыта", "Junior");
  choose("Формат работы", "Гибрид");
  choose("Период", "в год");
  fireEvent.submit(screen.getByRole("form"));
  expect(save.mock.calls[0][0]).toMatchObject({
    kind: "profile",
    value: {
      experience: "junior",
      workplace_preference: "hybrid",
      salary_period: "year",
    },
  });
});
it("CV work dropdown saves its canonical enum", () => {
  const save = vi.fn();
  render(
    <CVPreviewEditor
      preview={cvPreview}
      target={{ kind: "work", index: 0 }}
      pending={false}
      errors={{}}
      message=""
      onSave={save}
      onCancel={vi.fn()}
    />,
  );
  choose("Тип занятости", "Фриланс");
  fireEvent.submit(screen.getByRole("form"));
  expect(save.mock.calls[0][0]).toMatchObject({
    kind: "work",
    index: 0,
    value: { engagement_kind: "freelance" },
  });
});
it("CV language level keeps free text and selects canonical suggestions", () => {
  const save = vi.fn();
  render(
    <CVPreviewEditor
      preview={cvPreview}
      target={{ kind: "profile", field: "languages" }}
      pending={false}
      errors={{}}
      message=""
      onSave={save}
      onCancel={vi.fn()}
    />,
  );
  const level = screen.getByRole("combobox", { name: "Уровень" });
  fireEvent.change(level, { target: { value: "Professional working" } });
  fireEvent.submit(screen.getByRole("form"));
  expect(save.mock.calls[0][0].value.languages[0].level).toBe(
    "Professional working",
  );
  fireEvent.click(level);
  fireEvent.click(screen.getByRole("option", { name: "C1" }));
  fireEvent.submit(screen.getByRole("form"));
  expect(save.mock.calls[1][0].value.languages[0].level).toBe("C1");
});
