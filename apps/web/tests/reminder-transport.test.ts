// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("../lib/server/auth", async () => ({
  getDomainIdentity: vi.fn((await import("./session-fixture")).sessionIdentity),
}));
import { PATCH } from "../app/api/applications/[applicationId]/route";
import { GET } from "../app/api/applications/follow-ups/route";
import { saved } from "./fixtures";

const fields = {
  next_action_remind_at: "2026-10-10T07:02:00Z",
  next_action_timezone: "Asia/Yerevan",
  reminder_delivery_state: "pending",
  reminder_sent_at: null,
  reminder_failure_reason: null,
};
beforeEach(() => {
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "http://localhost:3100");
  vi.stubEnv("API_BASE_URL", "http://127.0.0.1:8000");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
const patch = (body: object) =>
  PATCH(
    new Request("http://localhost:3100/api/applications/42", {
      method: "PATCH",
      headers: {
        origin: "http://localhost:3100",
        "content-type": "application/json",
      },
      body: JSON.stringify(body),
    }),
    { params: Promise.resolve({ applicationId: "42" }) },
  );
it("forwards schedule fields under session identity and projects all internal fields away", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      ...saved,
      application: {
        ...saved.application,
        ...fields,
        generation: "private",
        lease_token: "private",
        attempt_count: 3,
        telegram_id: 123,
        user_id: 987,
        next_action_suggestions: [
          {
            id: "suggest",
            label: "Call",
            action_text: "Call",
            provider: "private",
          },
        ],
      },
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const response = await patch({
    next_action: "Call",
    next_action_remind_at: fields.next_action_remind_at,
    next_action_timezone: fields.next_action_timezone,
  });
  expect(response.status).toBe(200);
  const text = JSON.stringify(await response.json());
  expect(text).not.toMatch(
    /generation|lease_token|attempt_count|telegram_id|user_id|private/,
  );
  expect(fetcher.mock.calls[0][0]).toBe(
    "http://127.0.0.1:8000/users/987/applications/42",
  );
});
it.each([
  { generation: "x" },
  { chat_id: 1 },
  { lease_token: "x" },
  { next_action_remind_at: 1 },
  { next_action_timezone: [] },
])("rejects %j before API", async (body) => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect((await patch(body)).status).toBe(422);
  expect(fetcher).not.toHaveBeenCalled();
});
it("uses existing follow-ups with bounded timezone/bucket contract and public projection", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      items: [
        {
          application_id: 42,
          title: "Engineer",
          company: null,
          status: "applied",
          next_action: "Call",
          next_action_due_on: null,
          due_state: "today",
          ...fields,
          generation: "private",
          telegram_id: 123,
        },
      ],
      has_next: false,
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(
    new Request(
      "http://localhost:3100/api/applications/follow-ups?timezone=Asia%2FYerevan&bucket=today",
    ),
  );
  expect(response.status).toBe(200);
  expect(response.headers.get("Cache-Control")).toBe("no-store");
  expect(JSON.stringify(await response.json())).not.toMatch(
    /private|telegram_id|generation/,
  );
  const url = new URL(fetcher.mock.calls[0][0]);
  expect(url.pathname).toBe("/users/987/applications/follow-ups");
  expect(url.searchParams.get("limit")).toBe("5");
  expect(url.searchParams.get("timezone")).toBe("Asia/Yerevan");
});
it.each([
  "?user_id=1",
  "?timezone=UTC&bucket=today&chat_id=1",
  "?timezone=UTC&timezone=UTC&bucket=today",
  "?timezone=UTC&bucket=unknown",
])("rejects query authority %s", async (query) => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect(
    (
      await GET(
        new Request(
          "http://localhost:3100/api/applications/follow-ups" + query,
        ),
      )
    ).status,
  ).toBe(400);
  expect(fetcher).not.toHaveBeenCalled();
});
