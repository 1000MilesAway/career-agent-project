/** The shapes the API actually returns.
 *
 * Mirrors app/api/*.py. Written by hand rather than generated from
 * openapi.json on purpose: /api/state is typed `dict[str, Any]` on the server,
 * so a generator would produce `unknown` for the one payload the whole screen
 * is built on.
 */

export type Verdict = 'apply' | 'apply_with_caveats' | 'skip'
export type TriageBucket = 'worth' | 'maybe' | 'skip'
export type ApplyStepName = 'letter' | 'tailor' | 'interview'
/** The keys artifacts are stored under in /api/state. */
export type ArtifactKind = 'cover_letter' | 'tailored_resume' | 'interview_prep'

/** How a requirement weighs in the evaluation — the five bands from CO. */
export type Importance = 'critical' | 'high' | 'meaningful' | 'preferred' | 'low_signal'
export type MatchLevel = 'full' | 'partial' | 'none'

export interface Requirement {
  text: string
  /** Five bands since 2026-09-18; older stored reports still say must/nice. */
  weight?: Importance | 'must' | 'nice'
  importance?: Importance
  match: MatchLevel
  evidence: string
  gap_advice?: string | null
  basis?: 'stated' | 'structural' | 'inferred'
  jd_signal?: string | null
}

export interface Report {
  score: number
  verdict: Verdict
  role_summary: string
  requirements?: Requirement[]
  red_flags?: string[]
  salary?: {
    vacancy_range?: string | null
    market_estimate?: string | null
    comment: string
  } | null
  questions_to_employer?: string[]
}

export interface CoverLetter {
  letter: string
  angles?: string[]
}

export interface TailoredResume {
  summary?: string
  experience?: {
    role: string
    company: string
    period: string
    bullets?: string[]
  }[]
  skills?: string[]
  ats_keywords?: string[]
  /** What the model changed and why — where an invention would show up. */
  changes?: string[]
}

export interface InterviewPrep {
  questions?: { question: string; why: string; answer_hint: string }[]
  star_stories?: {
    title: string
    situation: string
    task: string
    action: string
    result: string
  }[]
  to_brush_up?: string[]
}

export type ArtifactContent = CoverLetter | TailoredResume | InterviewPrep

export interface FoundVacancy {
  id: string
  url: string | null
  title: string
  company: string | null
  qualification: string | null
  salary_text: string | null
  remote: boolean | null
  source: string
  /** collected | screened_out | shortlist | triaged_out | evaluated */
  stage: string
  /** Why a free filter or triage dropped it. Never null without a reason. */
  screen_reason: string | null
  triage: { bucket: TriageBucket; reason: string | null } | null
  saved: boolean
  status: string | null
  score: number | null
  verdict: Verdict | null
  report: Report | null
  artifacts: Partial<Record<ArtifactKind, ArtifactContent>> | null
  rub: number | null
}

export interface ResumeProfile {
  name?: string
  target_role?: string
  /** The level the experience shows, as opposed to what the person calls it. */
  seniority?: string
  years_experience?: number
  field?: string
  directions?: string[]
  skills?: string[]
  experience?: {
    role: string
    company: string
    period: string
    achievements?: string[]
  }[]
}

export interface SearchProfile {
  queries?: string[]
  positive?: string[]
  negative?: string[]
  divisions_negative?: string[]
  min_qualification?: string | null
  salary_min?: number | null
  remote_only?: boolean | null
}

export interface ApplyStep {
  step: ApplyStepName
  title: string
  rub: number
  seconds: number
  hint: string
}

export interface TrackerStatus {
  title: string
  terminal: boolean
}

export interface Spend {
  found: number
  scored: number
  with_artifacts: number
  rub: number
  free_to_reopen: number
  /** null when no ceiling is configured. */
  limit_rub: number | null
  spent_total_rub: number
}

export interface AppState {
  has_resume: boolean
  resume_name: string | null
  profile: ResumeProfile | null
  search_profile: SearchProfile | null
  found: FoundVacancy[]
  statuses: Record<string, TrackerStatus>
  apply_steps: ApplyStep[]
  spend: Spend
  user: { email: string; consent_at: string | null }
}

export interface ScanResult {
  collected: number
  dropped: number
  new: number
  channels?: string[]
  problems?: { channel?: string; why: string }[]
}

export interface TriageResult {
  judged: number
  counts?: Record<string, number>
  /** Postings the model returned no verdict for — shown, never backfilled. */
  missing?: string[]
  /** Ids the model invented. A broken prompt must look broken. */
  unknown?: string[]
  cost_rub?: number
  note?: string
}

export interface EvaluationResponse {
  id: string
  vacancy_id: string
  score: number | null
  verdict: Verdict | null
  report: Report
  cost_rub: number
  cached: boolean
}

export interface ArtifactResponse {
  id: string
  kind: ApplyStepName
  content: ArtifactContent
  cost_rub: number
  cached: boolean
}

export interface ResumeResponse {
  id: string
  filename: string
  profile: ResumeProfile
  search_profile: SearchProfile | null
  archetypes: Record<string, unknown> | null
}

export interface SearchProfileProposal {
  current: SearchProfile
  proposed: SearchProfile
}
