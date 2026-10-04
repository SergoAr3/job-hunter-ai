import type { ProfileDraft, WorkExperience } from "./profile";

export const CV_MAX_BYTES = 5 * 1024 * 1024;
export interface CVPreview {
  token: string;
  revision: number;
  expires_at: number;
  current: ProfileDraft | null;
  proposed: ProfileDraft;
  work_experience_mode: "replace";
  current_work_experience_count: number;
  experience_facts_mode: "replace";
  current_experience_fact_count: number;
  work_experience: Omit<WorkExperience, "duration_months">[];
  experience_facts: string[];
}
export type CVEdit =
  | { kind: "profile"; value: ProfileDraft }
  | { kind: "work"; index: number; value: CVPreview["work_experience"][number] }
  | { kind: "delete_work"; index: number }
  | { kind: "fact"; index: number; text: string }
  | { kind: "delete_fact"; index: number };
export function cvFileError(file: {
  name: string;
  size: number;
}): string | null {
  if (!/\.(pdf|docx)$/i.test(file.name)) return "unsupported_file_type";
  if (!file.size) return "cv_file_empty";
  if (file.size > CV_MAX_BYTES) return "file_too_large";
  return null;
}
