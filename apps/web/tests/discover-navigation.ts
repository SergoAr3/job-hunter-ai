import { useSyncExternalStore } from "react";
import { vi } from "vitest";

// Model Next's useSearchParams subscription; history is test-only.
let entries = ["/discover"];
let index = 0;
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};
const notify = () => listeners.forEach((listener) => listener());
export const navigationUrl = () => entries[index];
export const testRouter = {
  push: vi.fn((url: string) => {
    entries = [...entries.slice(0, index + 1), url];
    index++;
    notify();
  }),
  replace: vi.fn((url: string) => {
    entries[index] = url;
    notify();
  }),
};
export function resetNavigation(url = "/discover") {
  entries = [url];
  index = 0;
  testRouter.push.mockClear();
  testRouter.replace.mockClear();
  // Next patches native history to notify useSearchParams without an RSC trip.
  vi.spyOn(window.history, "replaceState").mockImplementation(
    (_data, _unused, url) => testRouter.replace(String(url)),
  );
}
export function back() {
  if (index > 0) {
    index--;
    notify();
  }
}
export function forward() {
  if (index < entries.length - 1) {
    index++;
    notify();
  }
}
export function useTestSearchParams() {
  const url = useSyncExternalStore(subscribe, navigationUrl, navigationUrl);
  return new URLSearchParams(url.split("?")[1] ?? "");
}
