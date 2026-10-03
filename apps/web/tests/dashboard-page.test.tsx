import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { WebError } from "../lib/errors";
vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({
  access: vi.fn(),
  summary: vi.fn(),
  list: vi.fn(),
  profile: vi.fn(),
}));
vi.mock("../lib/server/page-access", () => ({ pageAccess: mocks.access }));
vi.mock("../lib/server/api", () => ({
  getApplicationsSummary: mocks.summary,
  listApplications: mocks.list,
}));
vi.mock("../lib/server/profile", () => ({ getProfile: mocks.profile }));
vi.mock("next/navigation", () => ({
  redirect: (url: string) => {
    throw new Error(`REDIRECT:${url}`);
  },
}));
import Page from "../app/(workspace)/dashboard/page";
import { applicationStatuses } from "../lib/applications";
beforeEach(() => {
  vi.resetAllMocks();
  mocks.access.mockResolvedValue({ userId: "42" });
  mocks.summary.mockResolvedValue({
    total: 0,
    status_counts: Object.fromEntries(applicationStatuses.map((s) => [s, 0])),
  });
  mocks.list.mockResolvedValue({ items: [], has_next: false });
  mocks.profile.mockRejectedValue(new WebError("profile_missing", 404));
});
it("loads existing newest first page and allows absent profile", async () => {
  render(await Page());
  expect(mocks.access).toHaveBeenCalledWith("/dashboard");
  expect(mocks.list).toHaveBeenCalledWith({
    q: "",
    status: null,
    sort: "newest",
    offset: 0,
  });
  expect(screen.getByText("Начните поиск вакансий")).toBeInTheDocument();
  expect(screen.getByText("Заполнить профиль")).toBeInTheDocument();
});
it("auth unavailable does not fetch domain data or redirect", async () => {
  mocks.access.mockResolvedValue(null);
  render(await Page());
  expect(screen.getByText("Сервис временно недоступен")).toBeInTheDocument();
  expect(mocks.summary).not.toHaveBeenCalled();
});
it.each(["summary", "list", "profile"] as const)(
  "%s failure cannot masquerade as empty dashboard",
  async (key) => {
    mocks[key].mockRejectedValue(new WebError("api_unavailable", 503));
    render(await Page());
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Обзор временно недоступен",
    );
    expect(
      screen.queryByText("Начните поиск вакансий"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Повторить" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
  },
);
it("session revoked during domain loading redirects with safe next", async () => {
  mocks.list.mockRejectedValue(new WebError("unauthenticated", 401));
  await expect(Page()).rejects.toThrow("REDIRECT:/login?next=%2Fdashboard");
});
it("principal unavailable during loading retains auth unavailable UX", async () => {
  mocks.summary.mockRejectedValue(new WebError("auth_unavailable", 503));
  render(await Page());
  expect(screen.getByText("Сервис временно недоступен")).toBeInTheDocument();
});
