import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
vi.mock("next/navigation", () => ({ usePathname: () => "/profile" }));
import { Navigation } from "../components/navigation";

it("shows Profile after Applications and marks the active page", () => {
  render(<Navigation />);
  const links = screen.getAllByRole("link");
  expect(links.map((link) => link.textContent?.trim())).toEqual([
    "Поиск вакансий",
    "Мои вакансии",
    "Профиль",
  ]);
  expect(screen.getByRole("link", { name: "Профиль" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});
