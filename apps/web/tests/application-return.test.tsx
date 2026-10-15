import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { saved, vacancy } from "./fixtures";
import { WebError } from "../lib/errors";

vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({ access: vi.fn(), get: vi.fn() }));
vi.mock("../lib/server/page-access", () => ({ pageAccess: mocks.access }));
vi.mock("../lib/server/api", () => ({ getApplication: mocks.get }));
vi.mock("../components/application-status-detail", () => ({
  ApplicationStatusDetail: () => <p>Application</p>,
}));
vi.mock("next/navigation", () => ({
  redirect: (url: string) => {
    throw new Error(`REDIRECT:${url}`);
  },
  notFound: () => {
    throw new Error("NOT_FOUND");
  },
}));
import Page from "../app/(workspace)/applications/[applicationId]/page";
const selected = JSON.stringify([
  vacancy.source,
  vacancy.source_scope,
  vacancy.external_id,
]);
const from = `/discover?${new URLSearchParams({ query: "Python", remote_only: "true", offset: "20", selected })}`;
const props = (value?: string | string[]) => ({
  params: Promise.resolve({ applicationId: "42" }),
  searchParams: Promise.resolve({ from: value }),
});
beforeEach(() => {
  vi.clearAllMocks();
  mocks.access.mockResolvedValue({ user: { telegram_linked: true } });
  mocks.get.mockResolvedValue(saved);
});
it("returns to the exact validated Discover state", async () => {
  render(await Page(props(from)));
  expect(
    screen.getByRole("link", { name: "← К результатам поиска" }),
  ).toHaveAttribute("href", from);
  expect(mocks.access).toHaveBeenCalledWith(
    `/applications/42?from=${encodeURIComponent(from)}`,
  );
});
it("keeps the existing Applications list return behavior", async () => {
  const list = "/applications?q=Python&status=offer&sort=oldest&offset=10";
  render(await Page(props(list)));
  expect(
    screen.getByRole("link", { name: "← К моим вакансиям" }),
  ).toHaveAttribute("href", list);
});
it.each([undefined, "https://evil.test", "/discover?query=x&next=//evil.test"])(
  "falls back to Applications for unsupported from %s",
  async (value) => {
    render(await Page(props(value)));
    expect(
      screen.getByRole("link", { name: "← К моим вакансиям" }),
    ).toHaveAttribute("href", "/applications");
  },
);
it("preserves safe return context through detail retry and provides the back link on failure", async () => {
  mocks.get.mockRejectedValue(new WebError("api_unavailable", 503));
  render(await Page(props(from)));
  expect(screen.getByRole("link", { name: "Повторить" })).toHaveAttribute(
    "href",
    `/applications/42?from=${encodeURIComponent(from)}`,
  );
  expect(
    screen.getByRole("link", { name: "← К результатам поиска" }),
  ).toHaveAttribute("href", from);
});
it("preserves context when the session expires during detail loading", async () => {
  mocks.get.mockRejectedValue(new WebError("unauthenticated", 401));
  const detailUrl = `/applications/42?from=${encodeURIComponent(from)}`;
  await expect(Page(props(from))).rejects.toThrow(
    `REDIRECT:/login?next=${encodeURIComponent(detailUrl)}`,
  );
});

it("retains the full supported Unicode query through a login round trip", async () => {
  const longFrom = `/discover?${new URLSearchParams({ query: "開".repeat(200), remote_only: "true", offset: "20", selected })}`;
  mocks.get.mockRejectedValue(new WebError("unauthenticated", 401));
  const detailUrl = `/applications/42?from=${encodeURIComponent(longFrom)}`;
  expect(detailUrl.length).toBeGreaterThan(2048);
  await expect(Page(props(longFrom))).rejects.toThrow(
    `REDIRECT:/login?next=${encodeURIComponent(detailUrl)}`,
  );
});
