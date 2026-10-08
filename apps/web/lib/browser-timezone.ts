"use client";
import { useSyncExternalStore } from "react";

const subscribe = () => () => {};
function detectedZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
}
function availableZones() {
  try {
    return JSON.stringify(Intl.supportedValuesOf("timeZone"));
  } catch {
    return "[]";
  }
}
export function useBrowserTimezone() {
  return useSyncExternalStore(subscribe, detectedZone, () => "");
}
export function useAvailableTimezones(): string[] {
  const value = useSyncExternalStore(subscribe, availableZones, () => "[]");
  return JSON.parse(value);
}

let currentTime = 0;
const clockListeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;
function subscribeClock(listener: () => void) {
  clockListeners.add(listener);
  if (!timer) {
    currentTime = Date.now();
    timer = setInterval(() => {
      currentTime = Date.now();
      clockListeners.forEach((notify) => notify());
    }, 30000);
  }
  return () => {
    clockListeners.delete(listener);
    if (!clockListeners.size) {
      clearInterval(timer);
      timer = undefined;
    }
  };
}
export function useCurrentTime() {
  return useSyncExternalStore(
    subscribeClock,
    () => currentTime,
    () => 0,
  );
}
