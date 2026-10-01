import { expect, it } from "vitest";
import { validateProfileInput, type ProfileInput } from "../lib/profile";

const profile: ProfileInput = {
  target_roles: ["Engineer"],
  skills: [],
  experience: "unknown",
  location: ["Yerevan"],
  workplace_preference: "any",
  salary_min: "1000",
  salary_currency: "USD",
  salary_period: "month",
  languages: [],
};

it.each(["USD", "AMD", "EUR"])("accepts active currency %s", (currency) => {
  expect(
    validateProfileInput({ ...profile, salary_currency: currency }).payload
      ?.salary_currency,
  ).toBe(currency);
});

it("rejects a nonexistent currency before PUT", () => {
  expect(
    validateProfileInput({ ...profile, salary_currency: "ZZZ" }).errors
      .salary_currency,
  ).toBe("Укажите действующий код валюты.");
});

it("rejects workplace words as locations but keeps geographic locations", () => {
  expect(
    validateProfileInput({ ...profile, location: ["  REMOTE  "] }).errors
      .location,
  ).toMatch(/Формат работы/);
  expect(
    validateProfileInput({ ...profile, location: ["  ГИБРИД  "] }).errors
      .location,
  ).toMatch(/Формат работы/);
  expect(
    validateProfileInput({ ...profile, location: ["Yerevan"] }).payload
      ?.location,
  ).toEqual(["Yerevan"]);
});
