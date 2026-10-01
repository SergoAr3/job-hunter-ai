import { useState } from "react";
import { expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TagEditor } from "../components/tag-editor";

function setup(initial: string[], maxItems = 30) {
  const changed = vi.fn();
  function Fixture() {
    const [values, setValues] = useState(initial);
    const [inputValue, setInputValue] = useState("");
    return (
      <TagEditor
        id="tag-test"
        label="Навык"
        itemName="навык"
        values={values}
        inputValue={inputValue}
        onInputChange={setInputValue}
        onChange={(next) => {
          changed(next);
          setValues(next);
        }}
        maxItems={maxItems}
        maxLength={100}
        placeholder="Добавить навык…"
        disabled={false}
      />
    );
  }
  render(<Fixture />);
  return { changed, input: screen.getByRole("textbox", { name: "Навык" }) };
}

it("shows chips and adds only trimmed, nonempty, exact-unique values with Enter", () => {
  const { input, changed } = setup(["Python"]);
  expect(
    screen.getByRole("button", { name: "Удалить навык Python" }),
  ).toBeInTheDocument();
  expect(input).toHaveAttribute("id", "tag-test");
  input.focus();
  fireEvent.change(input, { target: { value: "  SQL  " } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(changed).toHaveBeenLastCalledWith(["Python", "SQL"]);
  expect(input).toHaveValue("");
  expect(input).toHaveFocus();
  fireEvent.change(input, { target: { value: "  " } });
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.change(input, { target: { value: " SQL " } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(screen.getAllByRole("button", { name: /Удалить навык/ })).toHaveLength(
    2,
  );
  fireEvent.change(input, { target: { value: "sql" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(
    screen.getByRole("button", { name: "Удалить навык sql" }),
  ).toBeInTheDocument();
});

it("enforces 30 items and returns focus to the input after removal", async () => {
  const { input } = setup(
    Array.from({ length: 30 }, (_, index) => `Skill ${index + 1}`),
  );
  expect(input).toBeDisabled();
  expect(input).toHaveAttribute("maxlength", "100");
  expect(screen.getByText("30 / 30")).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Удалить навык Skill 30" }),
  );
  expect(input).toBeEnabled();
  await waitFor(() => expect(input).toHaveFocus());
  fireEvent.change(input, { target: { value: "Redis" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(input).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Удалить навык Redis" }),
  ).toBeInTheDocument();
});

it("associates a field error with the input", () => {
  render(
    <TagEditor
      id="tag-error"
      label="Роль"
      itemName="роль"
      values={[]}
      inputValue=""
      onInputChange={vi.fn()}
      onChange={vi.fn()}
      maxItems={30}
      maxLength={100}
      placeholder="Добавить роль…"
      error="Укажите хотя бы одну роль."
      disabled={false}
    />,
  );
  const input = screen.getByRole("textbox", { name: "Роль" });
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input).toHaveAttribute("aria-describedby", "tag-error-error");
  expect(screen.getByText("Укажите хотя бы одну роль.")).toHaveAttribute(
    "id",
    "tag-error-error",
  );
});
