import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
const resolver = vi.hoisted(() => vi.fn());
vi.mock("../lib/server/auth", () => ({ getCurrentUser: resolver }));
vi.mock("../components/navigation", () => ({
  Navigation: () => <nav aria-label="Главная навигация" />,
}));
import WorkspaceLayout from "../app/(workspace)/layout";
it("keeps logout available on server outage without presenting dev identity", async () => {
  resolver.mockResolvedValue({ kind: "unavailable" });
  render(
    await WorkspaceLayout({ children: <p>Сервис временно недоступен</p> }),
  );
  expect(screen.getByRole("button", { name: "Выйти" })).toBeInTheDocument();
  expect(screen.queryByText("Локальный dev-аккаунт")).not.toBeInTheDocument();
});
it("no real cookie retains the explicit development account affordance", async () => {
  resolver.mockResolvedValue({ kind: "unauthenticated", present: false });
  render(await WorkspaceLayout({ children: <p>Профиль</p> }));
  expect(screen.getByText("Локальный dev-аккаунт")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Войти по email" }),
  ).toBeInTheDocument();
});
