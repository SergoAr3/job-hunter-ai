// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cvPreview } from "./cv-import-fixture";
import { WebError } from "../lib/errors";
import { CV_MAX_BYTES } from "../lib/cv-import";
vi.mock("server-only", () => ({}));
const identity = vi.hoisted(() => vi.fn());
vi.mock("../lib/server/auth", () => ({ getDomainIdentity: identity }));
import {
  uploadCV,
  importAction,
  projectPreview,
  editCV,
} from "../lib/server/cv-import";
beforeEach(() => {
  vi.stubEnv("WEB_PUBLIC_ORIGIN", "http://localhost");
  identity.mockResolvedValue({
    userId: "42",
    baseUrl: "http://api",
    headers: { Authorization: "Bearer server-session" },
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it("bounded edit uses authenticated PATCH and forwards safe field errors", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ ...cvPreview, revision: 2 }))
    .mockResolvedValueOnce(
      Response.json(
        {
          detail: {
            code: "cv_import_edit_invalid",
            fieldErrors: { dates: "Проверьте даты.", private: "secret" },
            input: "private",
          },
        },
        { status: 422 },
      ),
    );
  vi.stubGlobal("fetch", fetcher);
  const req = () =>
    new Request("http://localhost/api/profile/import/preview", {
      method: "PATCH",
      headers: {
        Origin: "http://localhost",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        token: cvPreview.token,
        revision: 1,
        edit: { kind: "delete_work", index: 0 },
      }),
    });
  expect((await editCV(req())).status).toBe(200);
  expect(fetcher.mock.calls[0][0]).toBe(
    "http://api/users/42/profile/cv-import/preview",
  );
  expect(fetcher.mock.calls[0][1].method).toBe("PATCH");
  const error = await editCV(req());
  expect(await error.json()).toEqual({
    code: "cv_import_edit_invalid",
    fieldErrors: { dates: "Проверьте даты." },
  });
  const cross = req();
  cross.headers.set("Origin", "https://evil.example");
  expect((await editCV(cross)).status).toBe(403);
});
function request(extra = false) {
  const form = new FormData();
  form.set(
    "file",
    new File(["pdf"], "resume.pdf", { type: "application/pdf" }),
  );
  if (extra) form.set("user_id", "999");
  return new Request("http://localhost/api/profile/import", {
    method: "POST",
    headers: { Origin: "http://localhost" },
    body: form,
  });
}
function actionRequest(value: unknown) {
  return new Request("http://localhost/api/profile/import/apply", {
    method: "POST",
    headers: { Origin: "http://localhost", "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
}
it("forwards bounded multipart to authenticated owner, strips server metadata", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      ...cvPreview,
      user_id: 999,
      fingerprint: "private",
      work_experience: [{ ...cvPreview.work_experience[0], id: 45 }],
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const response = await uploadCV(request());
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual(cvPreview);
  expect(fetcher.mock.calls[0][0]).toBe(
    "http://api/users/42/profile/cv-import",
  );
  expect(fetcher.mock.calls[0][1]).toEqual(
    expect.objectContaining({
      method: "POST",
      cache: "no-store",
      headers: { Authorization: "Bearer server-session" },
    }),
  );
  expect(fetcher.mock.calls[0][1].body.get("file").name).toBe("resume.pdf");
});
it.each(["unauthenticated", "auth_unavailable"])(
  "%s blocks before document reading/upstream",
  async (code) => {
    identity.mockRejectedValue(
      new WebError(code, code === "unauthenticated" ? 401 : 503),
    );
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const response = await uploadCV(request());
    expect((await response.json()).code).toBe(code);
    expect(fetcher).not.toHaveBeenCalled();
  },
);
it("rejects browser-selected owner and cross-origin upload", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect((await uploadCV(request(true))).status).toBe(400);
  const cross = request();
  cross.headers.set("Origin", "https://evil.example");
  expect((await uploadCV(cross)).status).toBe(403);
  expect(fetcher).not.toHaveBeenCalled();
});
it("bounds unknown-length body before multipart parsing", async () => {
  let cancelled = false;
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(CV_MAX_BYTES + 128 * 1024 + 1));
    },
    cancel() {
      cancelled = true;
    },
  });
  const req = new Request("http://localhost/api/profile/import", {
    method: "POST",
    headers: {
      Origin: "http://localhost",
      "Content-Type": "multipart/form-data; boundary=test",
    },
    body: stream,
    duplex: "half",
  } as RequestInit);
  const response = await uploadCV(req);
  expect(response.status).toBe(413);
  expect(cancelled).toBe(true);
});
it("Apply accepts token only, forwards no browser identity", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  expect(
    (
      await importAction(
        actionRequest({ token: cvPreview.token, proposed: {}, user_id: 999 }),
        "/apply",
      )
    ).status,
  ).toBe(400);
  expect(fetcher).not.toHaveBeenCalled();
  expect(
    (await importAction(actionRequest({ token: cvPreview.token }), "/apply"))
      .status,
  ).toBe(200);
  expect(fetcher.mock.calls[0][0]).toBe(
    "http://api/users/42/profile/cv-import/apply",
  );
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    token: cvPreview.token,
  });
});
it.each([401, 503, 422])(
  "projects safe upstream error %s without raw content",
  async (status) => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          Response.json(
            { detail: { code: "no_extractable_text", input: "private CV" } },
            { status },
          ),
        ),
    );
    const response = await uploadCV(request());
    expect(await response.json()).toEqual({
      code: status === 401 ? "unauthenticated" : "no_extractable_text",
    });
  },
);
it("malformed preview cannot masquerade as success", () => {
  expect(() => projectPreview({ ...cvPreview, proposed: {} })).toThrow();
});
it.each([
  { work_experience_mode: "append" },
  { current_work_experience_count: -1 },
  { current_work_experience_count: 1.5 },
  { current_work_experience_count: undefined },
  { experience_facts_mode: "append" },
  { current_experience_fact_count: -1 },
  { current_experience_fact_count: undefined },
])(
  "requires explicit snapshot semantics and safe current count: %j",
  (changes) => {
    expect(() => projectPreview({ ...cvPreview, ...changes })).toThrow();
  },
);
