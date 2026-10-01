import { useState } from "react";
import { expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { StatusSelect } from "../components/status-select";
import type { ApplicationStatus } from "../lib/applications";

function setup() {
  const changed = vi.fn();
  function Fixture() {
    const [value, setValue] = useState<ApplicationStatus>("saved");
    return (
      <>
        <label id="status-label" htmlFor="application-status">
          Статус
        </label>
        <StatusSelect
          value={value}
          disabled={false}
          labelId="status-label"
          onChange={(status) => {
            changed(status);
            setValue(status);
          }}
        />
        <button type="button">Следующий элемент</button>
      </>
    );
  }
  render(<Fixture />);
  return {
    changed,
    trigger: screen.getByRole("combobox", { name: /^Статус / }),
  };
}

it("opens a labelled listbox with all statuses and the current selection", () => {
  const { trigger, changed } = setup();
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(trigger).toHaveAttribute("aria-haspopup", "listbox");
  expect(trigger).toHaveTextContent("Сохранена");
  fireEvent.click(trigger);
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  const listbox = screen.getByRole("listbox", { name: "Статус" });
  expect(trigger).toHaveAttribute("aria-controls", listbox.id);
  expect(screen.getAllByRole("option")).toHaveLength(8);
  expect(screen.getByRole("option", { name: "Сохранена" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(changed).not.toHaveBeenCalled();
});

it("moves the active option with arrows and Home/End, then selects with Enter", () => {
  const { trigger, changed } = setup();
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  const active = () =>
    document.getElementById(trigger.getAttribute("aria-activedescendant")!);
  expect(active()).toHaveTextContent("Отклик отправлен");
  fireEvent.keyDown(trigger, { key: "ArrowUp" });
  expect(active()).toHaveTextContent("Сохранена");
  fireEvent.keyDown(trigger, { key: "End" });
  expect(active()).toHaveTextContent("Отказ");
  fireEvent.keyDown(trigger, { key: "Home" });
  expect(active()).toHaveTextContent("Сохранена");
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  fireEvent.keyDown(trigger, { key: "Enter" });
  expect(changed).toHaveBeenCalledWith("applied");
  expect(trigger).toHaveTextContent("Отклик отправлен");
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(trigger).toHaveFocus();
});

it("opens with Space, cancels with Escape, and closes on Tab or outside pointer", () => {
  const { trigger, changed } = setup();
  trigger.focus();
  fireEvent.keyDown(trigger, { key: " " });
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  fireEvent.keyDown(trigger, { key: "Escape" });
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(trigger).toHaveTextContent("Сохранена");
  expect(trigger).toHaveFocus();
  expect(changed).not.toHaveBeenCalled();

  fireEvent.click(trigger);
  fireEvent.keyDown(trigger, { key: "Tab" });
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(trigger);
  fireEvent.pointerDown(
    screen.getByRole("button", { name: "Следующий элемент" }),
  );
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(changed).not.toHaveBeenCalled();
});

it("selects a clicked option and returns focus to the trigger", () => {
  const { trigger, changed } = setup();
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole("option", { name: "Оффер" }));
  expect(changed).toHaveBeenCalledWith("offer");
  expect(trigger).toHaveTextContent("Оффер");
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(trigger).toHaveFocus();
});
