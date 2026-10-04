"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { webRequest } from "../lib/client";
import {
  CV_MAX_BYTES,
  cvFileError,
  type CVEdit,
  type CVPreview,
} from "../lib/cv-import";
import { CVPreviewEditor, type EditTarget } from "./cv-preview-editor";
import { WebError, errorMessage } from "../lib/errors";
import { CVDocumentIcon } from "./cv-document-icon";
import {
  experienceLabels,
  workplaceLabels,
  periodLabels,
  type ProfileDraft,
  type ProfileField,
} from "../lib/profile";

function profileValue(
  profile: ProfileDraft | null,
  field: ProfileField,
): string {
  if (!profile) return "Не указано";
  if (field === "experience") return experienceLabels[profile.experience];
  if (field === "workplace_preference")
    return workplaceLabels[profile.workplace_preference];
  if (field === "salary_min")
    return profile.salary_min === null
      ? "Не указано"
      : `${profile.salary_min} ${profile.salary_currency} ${periodLabels[profile.salary_period]}`;
  if (field === "languages")
    return (
      profile.languages
        .map((item) => `${item.language} · ${item.level}`)
        .join(", ") || "Не указано"
    );
  const value = profile[field];
  return (
    (Array.isArray(value) ? value.join(", ") : String(value ?? "")) ||
    "Не указано"
  );
}
function period(year: number | null, month: number | null) {
  return year === null
    ? "Дата не указана"
    : month === null
      ? String(year)
      : `${String(month).padStart(2, "0")}.${year}`;
}
const profileFields: [ProfileField, string][] = [
  ["target_roles", "Желаемые роли"],
  ["experience", "Уровень опыта"],
  ["location", "Локации"],
  ["workplace_preference", "Формат работы"],
  ["salary_min", "Зарплата от"],
];
function unchanged(preview: CVPreview, field: ProfileField) {
  if (!preview.current) return false;
  const value = (profile: ProfileDraft) =>
    field === "salary_min"
      ? [
          // API amounts may differ only in decimal formatting (3000 vs 3000.00).
          profile.salary_min === null
            ? null
            : String(profile.salary_min)
                .replace(/(\.\d*?)0+$/, "$1")
                .replace(/\.$/, ""),
          profile.salary_currency,
          profile.salary_period,
        ]
      : profile[field];
  return (
    JSON.stringify(value(preview.current)) ===
    JSON.stringify(value(preview.proposed))
  );
}
function PreviewValue({
  profile,
  field,
}: {
  profile: ProfileDraft | null;
  field: ProfileField;
}) {
  if (profile && field === "skills" && profile.skills.length)
    return (
      <ul className="profile-chips">
        {profile.skills.map((skill, index) => (
          <li key={`${skill}-${index}`}>{skill}</li>
        ))}
      </ul>
    );
  if (profile && field === "languages" && profile.languages.length)
    return (
      <ul className="profile-inline-list">
        {profile.languages.map((item, index) => (
          <li key={index}>
            <strong>{item.language}</strong> · {item.level}
          </li>
        ))}
      </ul>
    );
  return <span>{profileValue(profile, field)}</span>;
}
function Comparison({
  preview,
  fields,
}: {
  preview: CVPreview;
  fields: [ProfileField, string][];
}) {
  return (
    <dl className="cv-comparison">
      {fields.map(([field, label]) => (
        <div
          key={field}
          className={unchanged(preview, field) ? "cv-unchanged" : "cv-changed"}
        >
          <dt>{label}</dt>
          {unchanged(preview, field) ? (
            <dd>
              <PreviewValue profile={preview.proposed} field={field} />
              <span className="cv-unchanged-label">без изменений</span>
            </dd>
          ) : (
            <>
              <dd>
                <span className="cv-value-label">Сейчас</span>
                <PreviewValue profile={preview.current} field={field} />
              </dd>
              <dd>
                <span className="cv-value-label">После применения</span>
                <PreviewValue profile={preview.proposed} field={field} />
              </dd>
            </>
          )}
        </div>
      ))}
    </dl>
  );
}

export function CVImport() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CVPreview | null>(null);
  const [processing, setProcessing] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [editMessage, setEditMessage] = useState("");
  const [editErrors, setEditErrors] = useState<Record<string, string>>({});
  const returnFocus = useRef<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const busy = useRef(false);
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const mainChanges = preview
    ? profileFields.filter(([field]) => !unchanged(preview, field)).length
    : 0;
  useEffect(
    () => () => {
      sequence.current++;
      controller.current?.abort();
    },
    [],
  );
  useEffect(() => {
    if (preview) heading.current?.focus();
  }, [preview]);
  useEffect(() => {
    if (!editing && returnFocus.current) {
      (
        document.getElementById(returnFocus.current) ?? heading.current
      )?.focus();
      returnFocus.current = null;
    }
  }, [editing]);
  function editKey(target: EditTarget) {
    return target.kind === "profile"
      ? `profile-${target.field}`
      : `${target.kind}-${target.index}`;
  }
  function startEditing(target: EditTarget) {
    returnFocus.current = `cv-edit-${editKey(target)}`;
    setEditing(target);
    setEditMessage("");
    setEditErrors({});
  }
  const saveEdit = useCallback(
    async (edit: CVEdit) => {
      if (!preview || busy.current) return;
      busy.current = true;
      setPending(true);
      setEditMessage("");
      setEditErrors({});
      setError("");
      try {
        const updated = await webRequest<CVPreview>(
          "/api/profile/import/preview",
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              token: preview.token,
              revision: preview.revision,
              edit,
            }),
          },
        );
        setPreview(updated);
        setEditing(null);
      } catch (error) {
        const message = errorMessage(error);
        if (editing) {
          setEditMessage(message);
          setEditErrors(
            error instanceof WebError ? (error.fieldErrors ?? {}) : {},
          );
        } else setError(message);
      } finally {
        busy.current = false;
        setPending(false);
      }
    },
    [preview, editing],
  );
  function editor(target: EditTarget) {
    if (!preview || !editing || editKey(editing) !== editKey(target))
      return null;
    return (
      <CVPreviewEditor
        preview={preview}
        target={target}
        pending={pending}
        errors={editErrors}
        message={editMessage}
        onSave={saveEdit}
        onCancel={() => setEditing(null)}
        onDelete={
          target.kind === "profile"
            ? undefined
            : () =>
                void saveEdit({
                  kind: target.kind === "work" ? "delete_work" : "delete_fact",
                  index: target.index,
                })
        }
      />
    );
  }
  function editButton(target: EditTarget, label: string) {
    return (
      <button
        type="button"
        id={`cv-edit-${editKey(target)}`}
        aria-label={label}
        disabled={pending || !!editing}
        onClick={() => startEditing(target)}
      >
        {target.kind === "profile" ? label : "Изменить"}
      </button>
    );
  }

  function selectFile(selected: File | null) {
    setFile(selected);
    const invalid = selected && cvFileError(selected);
    setError(invalid ? errorMessage(new WebError(invalid)) : "");
  }

  async function extract(event: React.FormEvent) {
    event.preventDefault();
    if (!file || busy.current) return;
    const invalid = cvFileError(file);
    if (invalid) {
      setError(errorMessage(new WebError(invalid)));
      return;
    }
    const generation = ++sequence.current;
    controller.current = new AbortController();
    busy.current = true;
    setProcessing(true);
    setError("");
    const body = new FormData();
    body.set("file", file);
    try {
      const result = await webRequest<CVPreview>("/api/profile/import", {
        method: "POST",
        body,
        signal: controller.current.signal,
      });
      if (generation === sequence.current) {
        setPreview(result);
        setFile(null);
      }
    } catch (error) {
      if (generation === sequence.current) {
        setFile(null);
        setError(
          errorMessage(
            error instanceof WebError && error.code === "ambiguous_save"
              ? new WebError("cv_import_unavailable")
              : error,
          ),
        );
      }
    } finally {
      if (generation === sequence.current) {
        busy.current = false;
        setProcessing(false);
      }
    }
  }
  function stopProcessing() {
    sequence.current++;
    controller.current?.abort();
    busy.current = false;
    setProcessing(false);
    setFile(null);
    setError("");
  }
  function resetPreview() {
    setPreview(null);
    setFile(null);
    setError("");
    busy.current = false;
    setPending(false);
  }
  async function action(kind: "apply" | "cancel" | "replace") {
    if (!preview || busy.current || editing) return;
    busy.current = true;
    setPending(true);
    setError("");
    try {
      await webRequest(
        `/api/profile/import/${kind === "apply" ? "apply" : "cancel"}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            token: preview.token,
            revision: preview.revision,
          }),
        },
      );
      if (kind === "replace") {
        resetPreview();
        return;
      }
      window.location.assign(
        kind === "apply" ? "/profile?imported=1" : "/profile",
      );
    } catch (error) {
      if (
        kind === "replace" &&
        error instanceof WebError &&
        ["cv_import_invalid", "cv_import_used", "cv_import_expired"].includes(
          error.code,
        )
      ) {
        resetPreview();
        return;
      }
      setError(
        errorMessage(
          error instanceof WebError && error.code === "ambiguous_save"
            ? new WebError(
                kind === "apply"
                  ? "cv_import_unconfirmed"
                  : "cv_import_unavailable",
              )
            : error,
        ),
      );
      busy.current = false;
      setPending(false);
    }
  }
  return (
    <div className="profile-page cv-import-page">
      <Link className="back-link" href="/profile">
        ← Профиль
      </Link>
      <header className="page-heading">
        <div>
          <h1>Импорт резюме</h1>
          <p>
            Загрузите PDF или DOCX. Перед сохранением вы увидите распознанные
            данные.
          </p>
        </div>
      </header>
      <p className="cv-import-promise">
        Ничего не изменится, пока вы не нажмёте «Применить к профилю».
      </p>
      {error && (
        <div className="notice error" role="alert">
          <p>{error}</p>
        </div>
      )}
      {processing ? (
        <section className="dashboard-card cv-upload" aria-busy="true">
          <h2 role="status">Обрабатываем резюме…</h2>
          <p>Это может занять несколько секунд.</p>
          <button type="button" onClick={stopProcessing}>
            Отменить обработку
          </button>
        </section>
      ) : preview ? (
        <>
          <section className="dashboard-card cv-preview-section cv-preview-intro">
            <h2 ref={heading} tabIndex={-1}>
              Проверьте данные
            </h2>
            <p className="dashboard-muted">Предпросмотр действует 15 минут.</p>
            <ul className="cv-preview-summary" aria-label="После применения">
              <li>
                Основные данные:{" "}
                <strong>
                  {mainChanges
                    ? `${mainChanges} ${mainChanges === 1 ? "изменение" : mainChanges < 5 ? "изменения" : "изменений"}`
                    : "без изменений"}
                </strong>
              </li>
              <li>
                Навыки: <strong>{preview.proposed.skills.length}</strong> после
                применения
              </li>
              <li>
                Опыт работы:{" "}
                <strong>
                  {preview.current_work_experience_count} →{" "}
                  {preview.work_experience.length}
                </strong>
              </li>
              <li>
                Практический опыт:{" "}
                <strong>
                  {preview.current_experience_fact_count} →{" "}
                  {preview.experience_facts.length}
                </strong>
              </li>
            </ul>
          </section>
          {(preview.work_experience_mode === "replace" ||
            preview.experience_facts_mode === "replace") && (
            <section
              className="cv-replace-warning"
              aria-labelledby="cv-replace-warning-title"
            >
              <h2 id="cv-replace-warning-title">Важно</h2>
              <p>
                Опыт работы и практический опыт будут полностью заменены данными
                из этого резюме. Текущие записи, которых нет в новом резюме,
                будут удалены.
              </p>
              <p>
                Это касается и вручную добавленных записей опыта работы и
                практического опыта.
              </p>
              {preview.work_experience.length === 0 && (
                <p>
                  После применения текущий опыт работы будет удалён, потому что
                  в резюме не найдено записей опыта.
                </p>
              )}
              {preview.experience_facts.length === 0 && (
                <p>
                  После применения текущий практический опыт будет удалён,
                  потому что в резюме не найдено фактов практического опыта.
                </p>
              )}
            </section>
          )}
          <section className="dashboard-card cv-preview-section">
            <h2>Профиль</h2>
            {editor({ kind: "profile", field: "main" }) ?? (
              <>
                <Comparison preview={preview} fields={profileFields} />
                <div className="cv-edit-controls">
                  {editButton(
                    { kind: "profile", field: "main" },
                    "Изменить основные данные",
                  )}
                </div>
              </>
            )}
          </section>
          <section className="dashboard-card cv-preview-section">
            <h2>Навыки и языки</h2>
            {editor({ kind: "profile", field: "skills" }) ??
              editor({ kind: "profile", field: "languages" }) ?? (
                <>
                  <Comparison
                    preview={preview}
                    fields={[
                      ["skills", "Навыки"],
                      ["languages", "Языки"],
                    ]}
                  />
                  <div className="cv-edit-controls">
                    {editButton(
                      { kind: "profile", field: "skills" },
                      "Изменить навыки",
                    )}
                    {editButton(
                      { kind: "profile", field: "languages" },
                      "Изменить языки",
                    )}
                  </div>
                </>
              )}
          </section>
          <section className="dashboard-card cv-preview-section">
            <h2>Опыт работы</h2>
            <p className="dashboard-muted">
              После применения текущий опыт работы будет заменён данными из
              этого резюме. Это относится ко всем существующим записям, включая
              добавленные вручную.
            </p>
            <p>
              Сейчас: {preview.current_work_experience_count} · После
              применения: {preview.work_experience.length}
            </p>
            {preview.work_experience.length ? (
              <ul className="cv-work-list">
                {preview.work_experience.map((item, index) => (
                  <li key={index}>
                    {editor({ kind: "work", index }) ?? (
                      <>
                        <h3>{item.position || "Должность не указана"}</h3>
                        <p>{item.company || "Компания не указана"}</p>
                        <p className="dashboard-muted">
                          {period(item.start_year, item.start_month)} —{" "}
                          {item.is_current === true
                            ? "по настоящее время"
                            : period(item.end_year, item.end_month)}
                        </p>
                        <p className="dashboard-muted">
                          {
                            {
                              employment: "Работа",
                              internship: "Стажировка",
                              freelance: "Фриланс",
                              unknown: "Тип занятости не указан",
                            }[item.engagement_kind]
                          }
                        </p>
                        <div className="cv-edit-controls">
                          {editButton(
                            { kind: "work", index },
                            `Изменить опыт работы ${index + 1}`,
                          )}
                          <button
                            type="button"
                            aria-label={`Удалить опыт работы ${index + 1}`}
                            disabled={pending || !!editing}
                            onClick={() =>
                              void saveEdit({ kind: "delete_work", index })
                            }
                          >
                            Удалить
                          </button>
                        </div>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p>
                Опыт работы не распознан. После применения текущий список опыта
                работы будет пустым.
              </p>
            )}
          </section>
          <section className="dashboard-card cv-preview-section">
            <h2>Практический опыт</h2>
            <p className="dashboard-muted">
              После применения весь текущий практический опыт будет заменён
              фактами из этого резюме, включая факты, добавленные вручную.
            </p>
            <p>
              Сейчас: {preview.current_experience_fact_count} · После
              применения: {preview.experience_facts.length}
            </p>
            {preview.experience_facts.length ? (
              <ul className="cv-facts">
                {preview.experience_facts.map((text, index) => (
                  <li key={index}>
                    {editor({ kind: "fact", index }) ?? (
                      <>
                        <span>{text}</span>
                        <div className="cv-edit-controls">
                          {editButton(
                            { kind: "fact", index },
                            `Изменить факт ${index + 1}`,
                          )}
                          <button
                            type="button"
                            aria-label={`Удалить факт ${index + 1}`}
                            disabled={pending || !!editing}
                            onClick={() =>
                              void saveEdit({ kind: "delete_fact", index })
                            }
                          >
                            Удалить
                          </button>
                        </div>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p>
                Факты практического опыта не распознаны. После применения
                текущий список фактов будет пустым.
              </p>
            )}
          </section>
          <div
            className={`cv-import-actions${editing ? " has-editor" : ""}`}
            role="group"
            aria-label="Действия с резюме"
          >
            {editing && (
              <p className="cv-unsaved-edit" role="status">
                Сохраните или отмените изменения перед применением.
              </p>
            )}
            <button
              type="button"
              disabled={pending || !!editing}
              onClick={() => void action("replace")}
            >
              Выбрать другой файл
            </button>
            <button
              type="button"
              disabled={pending || !!editing}
              onClick={() => void action("cancel")}
              className="cv-cancel"
            >
              Отменить
            </button>
            <button
              type="button"
              className="primary"
              disabled={pending || !!editing}
              onClick={() => void action("apply")}
            >
              {pending ? "Подождите…" : "Применить к профилю"}
            </button>
          </div>
        </>
      ) : (
        <form className="dashboard-card cv-upload" onSubmit={extract}>
          <div
            className={`cv-upload-zone${file ? " has-file" : ""}${dragging ? " is-dragging" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              event.dataTransfer.dropEffect = "copy";
              setDragging(true);
            }}
            onDragLeave={(event) => {
              if (
                !event.currentTarget.contains(
                  event.relatedTarget as Node | null,
                )
              )
                setDragging(false);
            }}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              selectFile(event.dataTransfer.files[0] ?? null);
            }}
          >
            <span className="cv-document-icon">
              <CVDocumentIcon />
            </span>
            {file ? (
              <div className="cv-selected-file" role="status">
                <h2 title={file.name}>{file.name}</h2>
                <p>
                  {file.size < 1024 * 1024
                    ? `${Math.max(1, Math.ceil(file.size / 1024))} КБ`
                    : `${(file.size / 1024 / 1024).toFixed(1)} МБ`}
                </p>
                <p>
                  {cvFileError(file)
                    ? "Проверьте формат и размер файла"
                    : "Файл готов к распознаванию"}
                </p>
              </div>
            ) : (
              <>
                <h2>Загрузите резюме</h2>
                <p>Перетащите PDF или DOCX сюда</p>
                <span className="cv-upload-or">или</span>
              </>
            )}
            <label
              className={file ? "sr-only" : "cv-file-label"}
              htmlFor="cv-file"
            >
              Файл резюме
            </label>
            <input
              ref={fileInput}
              className="sr-only"
              id="cv-file"
              type="file"
              accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              aria-describedby="cv-file-help"
              onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
            />
            <button type="button" onClick={() => fileInput.current?.click()}>
              {file ? "Выбрать другой файл" : "Выбрать файл"}
            </button>
            <p id="cv-file-help" className={file ? "sr-only" : undefined}>
              PDF или DOCX · до {CV_MAX_BYTES / 1024 / 1024} МБ · сканы не
              поддерживаются
            </p>
          </div>
          <button
            type="submit"
            className="primary"
            disabled={!file || !!cvFileError(file)}
          >
            Распознать резюме
          </button>
        </form>
      )}
    </div>
  );
}
