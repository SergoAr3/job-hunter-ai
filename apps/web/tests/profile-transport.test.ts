// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { GET, PUT } from "../app/api/profile/route";
import { GET as getWork } from "../app/api/profile/work-experiences/route";
import { GET as getFacts } from "../app/api/profile/experience-facts/route";
import type { ProfileInput } from "../lib/profile";

const origin = "http://localhost:3100";
const url = `${origin}/api/profile`;
const payload: ProfileInput = {
  target_roles: ["Python Engineer"],
  skills: ["Python"],
  experience: "middle",
  location: ["Yerevan"],
  workplace_preference: "remote",
  salary_min: "2500.25",
  salary_currency: "USD",
  salary_period: "month",
  languages: [{ language: "English", level: "B2" }],
};
const saved = {
  ...payload,
  user_id: 987,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
  private_field: "hidden",
};
function mutation(body: unknown, address = url, from = origin) {
  return new Request(address, {
    method: "PUT",
    headers: { origin: from, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}
beforeEach(() => {
  vi.stubEnv("WEB_DEV_USER_ID", "987");
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("reads the configured user's profile and projects only display fields", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(saved));
  vi.stubGlobal("fetch", fetcher);
  const result = await GET(new Request(url));
  expect(result.status).toBe(200);
  expect(await result.json()).toEqual({
    ...payload,
    created_at: saved.created_at,
    updated_at: saved.updated_at,
  });
  expect(fetcher.mock.calls[0][0]).toBe(
    "http://127.0.0.1:8000/users/987/profile",
  );
  expect(fetcher.mock.calls[0][1]).toMatchObject({
    method: "GET",
    cache: "no-store",
    redirect: "error",
  });
});

it("accepts the API's legacy language response without rewriting it", async () => {
  const legacy = {
    ...saved,
    languages: [{ language: "English English", level: "English" }],
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(legacy)));
  const result = await GET(new Request(url));
  expect(result.status).toBe(200);
  expect((await result.json()).languages).toEqual(legacy.languages);
});

it("rejects user injection and treats initial 404 as missing profile", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      Response.json({ detail: "Profile not found" }, { status: 404 }),
    );
  vi.stubGlobal("fetch", fetcher);
  expect((await GET(new Request(`${url}?user_id=1`))).status).toBe(400);
  const missing = await GET(new Request(url));
  expect(missing.status).toBe(404);
  expect(await missing.json()).toEqual({ code: "profile_missing" });
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("sends the full profile under the configured user and confirms the PUT response shape", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(saved));
  vi.stubGlobal("fetch", fetcher);
  const result = await PUT(mutation({ ...payload, salary_currency: "usd" }));
  expect(await result.json()).toEqual({ ok: true });
  expect(fetcher.mock.calls[0][0]).toBe(
    "http://127.0.0.1:8000/users/987/profile",
  );
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(payload);
});

it.each([
  [{ ...payload, user_id: 1 }],
  [{ ...payload, target_roles: [] }],
  [{ ...payload, experience: "principal" }],
  [
    {
      ...payload,
      languages: [
        { language: "English", level: "B2" },
        { language: "english", level: "C1" },
      ],
    },
  ],
  [{ ...payload, salary_min: "2500.123" }],
  [{ ...payload, salary_currency: null }],
  [{ ...payload, skills: Array.from({ length: 31 }, () => "Python") }],
])("rejects malformed or partial profile before API: %j", async (body) => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect((await PUT(mutation(body))).status).toBe(422);
  expect(fetcher).not.toHaveBeenCalled();
});

it("requires same-origin JSON and rejects arbitrary user query", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect((await PUT(mutation(payload, url, "https://evil.test"))).status).toBe(
    403,
  );
  expect((await PUT(mutation(payload, `${url}?user_id=1`))).status).toBe(400);
  expect(
    (await PUT(new Request(url, { method: "PUT", body: "{}" }))).status,
  ).toBe(403);
  expect(fetcher).not.toHaveBeenCalled();
});

it("maps backend validation to safe field errors and distinguishes missing user", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json(
        {
          detail: [
            { loc: ["body", "salary_currency"], msg: "secret internal detail" },
          ],
        },
        { status: 422 },
      ),
    ),
  );
  const invalid = await PUT(mutation(payload));
  expect(invalid.status).toBe(422);
  const body = await invalid.json();
  expect(body).toEqual({
    code: "profile_invalid",
    fieldErrors: { salary_currency: "Проверьте поле «Валюта»." },
  });
  expect(JSON.stringify(body)).not.toContain("secret");
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ detail: "User not found" }, { status: 404 }),
      ),
  );
  expect(await (await PUT(mutation(payload))).json()).toEqual({
    code: "profile_user_not_found",
  });
});

it("marks uncertain transport and server outcomes without retrying PUT", async () => {
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new Error("timeout"))
    .mockResolvedValueOnce(Response.json({ detail: "error" }, { status: 503 }))
    .mockResolvedValueOnce(Response.json({}));
  vi.stubGlobal("fetch", fetcher);
  for (let i = 0; i < 3; i++)
    expect(await (await PUT(mutation(payload))).json()).toEqual({
      code: "ambiguous_profile",
    });
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it("reads both lists under the configured user and strips backend-only fields", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({
        items: [
          {
            id: 4,
            user_profile_id: 2,
            company: "Acme",
            position: "Engineer",
            engagement_kind: "employment",
            start_year: 2020,
            start_month: 1,
            end_year: null,
            end_month: null,
            is_current: true,
            duration_months: 81,
          },
        ],
      }),
    )
    .mockResolvedValueOnce(
      Response.json({
        items: [{ id: 5, text: "Built APIs", private_field: "hidden" }],
      }),
    );
  vi.stubGlobal("fetch", fetcher);
  const work = await getWork(new Request(`${url}/work-experiences`));
  const facts = await getFacts(new Request(`${url}/experience-facts`));
  expect((await work.json()).items[0]).not.toHaveProperty("id");
  expect(await facts.json()).toEqual({ items: [{ text: "Built APIs" }] });
  expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
    "http://127.0.0.1:8000/users/987/profile/work-experiences",
    "http://127.0.0.1:8000/users/987/profile/experience-facts",
  ]);
});
