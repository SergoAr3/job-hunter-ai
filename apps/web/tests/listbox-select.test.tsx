import { useState } from "react";
import { expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ListboxSelect } from "../components/listbox-select";

function setup({
  disabled = false,
  editable = false,
}: { disabled?: boolean; editable?: boolean } = {}) {
  const changed = vi.fn();
  function Fixture() {
    const [value, setValue] = useState("employment");
    return (
      <>
        <label id="kind-label" htmlFor="kind">
          Тип занятости
        </label>
        <p id="kind-help">Helper</p>
        <ListboxSelect<string>
          id="kind"
          labelId="kind-label"
          value={value}
          options={["employment", "internship", "unknown"]}
          labels={{
            employment: "Работа по найму",
            internship: "Стажировка",
            unknown: "Не указан",
          }}
          disabled={disabled}
          invalid
          describedBy="kind-help"
          editable={editable}
          maxLength={100}
          onChange={(next) => {
            changed(next);
            setValue(next);
          }}
        />
        <button>Next</button>
      </>
    );
  }
  const view = render(<Fixture />);
  return { ...view, changed, trigger: screen.getByRole("combobox") };
}

it("announces value and error helper, maps localized selection to canonical value", () => {
  const { trigger, changed } = setup();
  expect(trigger).toHaveAccessibleName("Тип занятости Работа по найму");
  expect(trigger).toHaveAttribute("aria-invalid", "true");
  expect(trigger).toHaveAccessibleDescription("Helper");
  fireEvent.click(trigger);
  expect(
    screen.getByRole("option", { name: "Работа по найму" }),
  ).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("option", { name: "Стажировка" }));
  expect(changed).toHaveBeenCalledWith("internship");
  expect(trigger).toHaveTextContent("Стажировка");
  expect(trigger).toHaveFocus();
});

it("Enter/Space open, arrows activate, Escape cancels and Tab/outside/blur close without a trap", () => {
  const { trigger, changed } = setup();
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  expect(
    document.getElementById(trigger.getAttribute("aria-activedescendant")!),
  ).toHaveTextContent("Стажировка");
  fireEvent.keyDown(trigger, { key: "Escape" });
  expect(changed).not.toHaveBeenCalled();
  fireEvent.keyDown(trigger, { key: " " });
  fireEvent.keyDown(trigger, { key: "ArrowUp" });
  fireEvent.keyDown(trigger, { key: "Enter" });
  expect(changed).toHaveBeenCalledWith("unknown");
  fireEvent.click(trigger);
  fireEvent.keyDown(trigger, { key: "Tab" });
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(trigger);
  fireEvent.pointerDown(screen.getByRole("button", { name: "Next" }));
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(trigger);
  fireEvent.blur(trigger, {
    relatedTarget: screen.getByRole("button", { name: "Next" }),
  });
  expect(trigger).toHaveAttribute("aria-expanded", "false");
});

it("disabled controls cannot open or select", () => {
  const { trigger, changed } = setup({ disabled: true });
  expect(trigger).toBeDisabled();
  fireEvent.click(trigger);
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(changed).not.toHaveBeenCalled();
});

it("editable suggestions preserve arbitrary input and spaces and support keyboard selection", () => {
  const { trigger, changed } = setup({ editable: true });
  expect(trigger).toHaveAttribute("aria-autocomplete", "list");
  fireEvent.change(trigger, { target: { value: "Custom level" } });
  expect(changed).toHaveBeenLastCalledWith("Custom level");
  expect(trigger).toHaveValue("Custom level");
  fireEvent.keyDown(trigger, { key: " " });
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  fireEvent.keyDown(trigger, { key: "Enter" });
  expect(changed).toHaveBeenLastCalledWith("internship");
});
