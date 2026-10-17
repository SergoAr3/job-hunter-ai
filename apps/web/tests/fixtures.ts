import type { Vacancy, DiscoverPage, SaveResult } from "../lib/contracts";
// Test-only data. Never imported by runtime code.
export const vacancy: Vacancy = {
  source: "trudvsem",
  source_scope: "company-1",
  external_id: "job-1",
  source_url: "https://trudvsem.ru/vacancy/card/company-1/job-1",
  title: "Python developer",
  company: "Test company",
  location: "Москва",
  workplace_type: "unknown",
  salary_text: null,
  salary_min: null,
  salary_max: null,
  salary_currency: null,
  description: "<script>external text</script>",
  requirements_text: "Python",
  already_saved_for_user: false,
  application_id: null,
  application_status: null,
  preview_match: {
    available: false,
    unavailable_reason: "profile_missing_for_preview",
    score: null,
    verdict: null,
    confidence: null,
    coverage: null,
    strengths: [],
    gaps: [],
    unknowns: [],
    conflicts: [],
    recommendation: null,
  },
};
export const page: DiscoverPage = {
  items: [vacancy],
  source_total: 1,
  returned_count: 1,
  limit: 10,
  offset: 0,
  next_offset: null,
  locally_filtered: false,
};
export const saved: SaveResult = {
  application: {
    id: 42,
    status: "interview",
    note: "Уточнить условия",
    next_action: "Написать рекрутеру",
    next_action_due_on: "2026-10-01",
  },
  job: vacancy,
  application_created: false,
  job_created: false,
};

export const feedPage: import("../lib/contracts").DiscoverFeedPage = {
  items: [{ ...vacancy, workplace_type: "remote" }],
  returned_count: 1,
  cursor: "A".repeat(32),
  prev_cursor: null,
  next_cursor: "B".repeat(32),
  next_action: "next",
  state: "page",
  expires_at: "2026-10-17T12:00:00Z",
};
