import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
import { Navigation } from "../components/navigation";

it("shows Dashboard first and marks the active page", () => {
  render(<Navigation />);
  const links = screen.getAllByRole("link");
  expect(links.map((link) => link.textContent?.trim())).toEqual([
    "Dashboard",
    "Найти вакансии",
    "Мои вакансии",
    "Профиль",
  ]);
  expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});
