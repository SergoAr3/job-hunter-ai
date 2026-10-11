import { type MatchResult } from "../lib/match-contract";
// API-shaped fixtures, never imported by production code.
export function matchFixture(): MatchResult {
  return {
    algorithm_version: "job-match-v2.1",
    application_id: 42,
    job_id: 2,
    score: 80,
    verdict: "high",
    coverage: 95,
    confidence: "high",
    input_state: {
      profile_updated_at: null,
      job_updated_at: null,
      parsing_status: "success",
      ai_enrichment_status: "success",
    },
    components: {
      role: {
        weight: 20,
        score: 100,
        status: "matched",
        matched: ["Python Backend Developer"],
        missing: [],
      },
      required_skills: {
        weight: 30,
        score: 50,
        status: "partial",
        matched: ["Python"],
        missing: ["Kubernetes"],
      },
      nice_to_have_skills: {
        weight: 5,
        score: 50,
        status: "partial",
        matched: ["Docker"],
        missing: ["Redis"],
      },
      seniority: {
        weight: 15,
        score: 100,
        status: "matched",
        matched: ["middle"],
        missing: [],
      },
      languages: {
        weight: 10,
        score: 100,
        status: "matched",
        matched: ["English B2"],
        missing: [],
      },
      workplace: {
        weight: 5,
        score: 100,
        status: "matched",
        matched: ["remote"],
        missing: [],
      },
      location: {
        weight: 5,
        score: null,
        status: "unknown",
        matched: [],
        missing: [],
      },
      salary: {
        weight: 10,
        score: 100,
        status: "matched",
        matched: [],
        missing: [],
      },
    },
    strengths: [
      {
        code: "required_skill_listed",
        component: "required_skills",
        value: "Python",
      },
    ],
    gaps: [
      {
        code: "required_skill_not_listed",
        component: "required_skills",
        value: "Kubernetes",
      },
    ],
    unknowns: [],
    conflicts: [],
    recommendation: { code: "apply", primary_reason: null },
    explainability: {
      schema_version: 1,
      components: {
        role: { notices: [], unknown_items: [] },
        required_skills: { notices: [], unknown_items: [] },
        nice_to_have_skills: { notices: [], unknown_items: [] },
        seniority: { notices: [], unknown_items: [] },
        languages: { notices: [], unknown_items: [] },
        workplace: { notices: [], unknown_items: [] },
        location: {
          notices: [
            {
              code: "remote_location_not_compared",
              source: "comparison",
              value: null,
            },
          ],
          unknown_items: [],
        },
        salary: { notices: [], unknown_items: [] },
      },
    },
  };
}
