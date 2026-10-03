// Display contracts mirror apps/api/app/schemas.py. Domain decisions stay in FastAPI.
export interface Reason {
  code: string;
  component: string;
  value?: string | null;
}
export interface Preview {
  available: boolean;
  unavailable_reason?: string | null;
  score: number | null;
  verdict: string | null;
  coverage: number | null;
  confidence: string | null;
  strengths: Reason[];
  gaps: Reason[];
  unknowns: Reason[];
  conflicts: Reason[];
  recommendation: { code: string; primary_reason?: Reason | null } | null;
}
export interface Vacancy {
  source: string;
  source_scope: string;
  external_id: string;
  source_url: string;
  title: string;
  company: string;
  description: string | null;
  requirements_text: string | null;
  location: string | null;
  workplace_type: string;
  salary_text: string | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  preview_match: Preview;
  already_saved_for_user: boolean;
}
export interface DiscoverPage {
  items: Vacancy[];
  source_total: number;
  returned_count: number;
  limit: number;
  offset: number;
  next_offset: number | null;
  locally_filtered: boolean;
}
export type Identity = Pick<Vacancy, "source" | "source_scope" | "external_id">;
export interface ApplicationDetail {
  application: {
    id: number;
    status: string;
    note: string | null;
    next_action: string | null;
    next_action_due_on: string | null;
  };
  job: {
    title: string | null;
    company: string | null;
    location: string | null;
    workplace_type: string;
    source_url: string;
    description: string | null;
    requirements_text: string | null;
    salary_text: string | null;
    salary_min: number | null;
    salary_max: number | null;
    salary_currency: string | null;
  };
}
export interface ApplicationStatusHistory {
  items: {
    status: string;
    occurred_at: string;
  }[];
}
export interface SaveResult extends ApplicationDetail {
  job_created: boolean;
  application_created: boolean;
}
export interface ApplicationListItem {
  app_id: number;
  status: string;
  created_at: string;
  title: string | null;
  company: string | null;
  location: string | null;
  workplace_type: string;
  parsing_status: string;
  ai_enrichment_status: string;
}
export interface ApplicationsPage {
  items: ApplicationListItem[];
  has_next: boolean;
}

export interface ApplicationsSummary {
  total: number;
  status_counts: Record<import("./applications").ApplicationStatus, number>;
}
