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
/** The keys artifacts are stored under in /api/state.
 *
 * The SAME strings as the step names: the server keys the map by
 * `artifacts.kind`, which is what `/api/artifacts/{id}/{kind}` was called
 * with. They were once spelled out longhand here, and every lookup silently
 * returned undefined — a bought document never showed as bought. */
export type ArtifactKind = ApplyStepName

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
  /** The band in roubles a month, [from, to]; a null end is not stated.
   *  Absent or both null — no rouble band stated. */
  salary_range?: [number | null, number | null] | null
  /** Why the text looks like fraud; a mark on the card, never a drop. */
  fraud?: string | null
  remote: boolean | null
  /** The city as the source states it; empty or null — the source is silent. */
  location?: string | null
  /** IT accreditation as the source states it; null — the source is silent. */
  accredited?: boolean | null
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
  /** The score was bought against an earlier CV or revision of it. */
  outdated?: boolean
  /** The row this one copies, when the scan found it elsewhere first. The
   * reason is in `screen_reason`; the id lets the row point at the original. */
  duplicate_of?: string | null
  /** Set only on positive evidence (404/410, the page's archive banner). */
  closed?: { at: string; reason: string | null } | null
  /** Too old to trust, from a source that cannot be asked. Not closed. */
  stale?: string | null
  /** Hidden by the user; folded, never deleted. */
  hidden?: boolean
  published?: string | null
  /** Brought by the user (a link or a text): lives in «Мои вакансии». */
  added_at?: string | null
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
  /** The CV's own "О себе" paragraph, near-verbatim; null when it has none. */
  summary?: string | null
  experience?: {
    role: string
    company: string
    period: string
    location?: string | null
    /** One line on what the company or project does. */
    description?: string | null
    achievements?: string[]
  }[]
  education?: string[]
  languages?: string[]
  /** What the person asks of a job, as the CV states it. The cabinet edits
   *  only the city; the server keeps the other keys. */
  preferences?: { city?: string | null; [key: string]: unknown } | null
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
  seconds: number
  hint: string
}

export interface TrackerStatus {
  title: string
  terminal: boolean
}

/** One quota window. Shares only: the cabinet never shows money. */
export interface QuotaWindow {
  /** Whole percent, capped at 100. */
  used_pct: number
  /** When the window turns over, ISO with the Moscow offset. */
  resets_at: string
}

export interface Quota {
  plan: 'free' | 'paid'
  paid_until: string | null
  /** null when the window is off — no limit configured, or an admin. */
  day: QuotaWindow | null
  week: QuotaWindow | null
}

export interface Spend {
  found: number
  scored: number
  with_artifacts: number
  quota: Quota
}

/** One version of the CV: an upload, a chat edit or a rollback. */
export interface ResumeVersion {
  id: string
  revision: number
  filename: string
  created_at: string
  active: boolean
  /** What this version changed against the one before it, in words. */
  changes: string[]
}

export interface AppState {
  has_resume: boolean
  resume_name: string | null
  /** A portrait is uploaded for the resume header. */
  has_photo: boolean
  profile: ResumeProfile | null
  search_profile: SearchProfile | null
  /** Every version of the CV, newest first. */
  resume_history?: ResumeVersion[]
  found: FoundVacancy[]
  statuses: Record<string, TrackerStatus>
  apply_steps: ApplyStep[]
  spend: Spend
  user: {
    /** Null for an account that signed up with Telegram — it has no password either. */
    email: string | null
    /** What the user calls themselves; null until set — show the email then. */
    name?: string | null
    consent_at: string | null
    /** "@handle" of the linked Telegram account; null = not linked. */
    telegram_name?: string | null
    /** Shows the owner's dashboard; the API checks the role again. */
    is_admin?: boolean
  }
}

export interface ScanResult {
  collected: number
  dropped: number
  new: number
  /** Postings that copy a vacancy the user already has — not given a row of
   * their own; GET /api/vacancies/screened names the original. */
  duplicates?: number
  kept?: number
  /** Listings answered from a stale pool; a background refresh re-reads them. */
  deferred?: number
  channels?: string[]
  /** Telegram names the channel, employer boards the board. */
  problems?: { channel?: string; board?: string; why: string }[]
  /** A scan of everything: one entry per source, in the order written. */
  sources?: Record<string, SourceScan>
}

/** One source inside a scan of everything. `error` instead of counts when the
 *  source failed — the others still ran. */
export type SourceScan = Partial<ScanResult> & {
  collected?: number
  new?: number
  error?: string
}

/** A queued step, as GET /api/jobs returns it. The worker runs it; the
 *  cabinet polls the row. */
export type JobStatus = 'queued' | 'running' | 'done' | 'failed'

export interface Job<R = unknown> {
  id: string
  kind: string
  status: JobStatus
  attempts: number
  /** What the step returned — for a scan, the same shape the endpoint gave. */
  result: R | null
  /** Why it failed, readable as is. */
  error: string | null
  created_at: string
  finished_at: string | null
}

export type ScanJob = Job<ScanResult>

export type TriageJob = Job<TriageResult>

export interface RecheckResult {
  checked: number
  open: number
  closed: number
  unknown: number
}

export interface TriageResult {
  judged: number
  counts?: Record<string, number>
  /** Postings the model returned no verdict for — shown, never backfilled. */
  missing?: string[]
  /** Ids the model invented. A broken prompt must look broken. */
  unknown?: string[]
  note?: string
}

export interface EvaluationResponse {
  id: string
  vacancy_id: string
  score: number | null
  verdict: Verdict | null
  report: Report
  cached: boolean
}

export interface ArtifactResponse {
  id: string
  kind: ApplyStepName
  content: ArtifactContent
  cached: boolean
}

export interface ResumeResponse {
  id: string
  filename: string
  profile: ResumeProfile
  search_profile: SearchProfile | null
  archetypes: Record<string, unknown> | null
  revision: number
  parent_id: string | null
  is_active: boolean
  created_at: string
}

/** What a changed search profile did to the found list: free, from the pool. */
export interface Rematch {
  added: number
  removed: number
  reasons: { reason: string; count: number }[]
}

/** Upload and an accepted search profile also report the rematch;
 * null when the profile did not change. */
export interface ResumeWithRematch extends ResumeResponse {
  rematch: Rematch | null
}

export interface SearchProfileProposal {
  current: SearchProfile
  proposed: SearchProfile
}

/** The chat. Mirrors app/api/chat.py.
 *
 * A turn where the model only asked for tools carries no text and is folded
 * into the reply that followed, so every message here has something to show;
 * `tools` says what was consulted on the way. */
export interface ChatMessage {
  /** `event` is a note from the service, e.g. that the CV was replaced. */
  role: 'user' | 'assistant' | 'event'
  text: string
  created_at: string
  tools: string[]
  /** Answers to pick when the assistant ended its turn on a question. */
  choices?: string[]
  /** The vacancy a user message was written about ("Обсудить" in Избранном). */
  vacancy?: { id: string; title: string; company: string | null } | null
}

/** What the chat prepared and the user has to confirm.
 *
 * Nothing is written until it is accepted, and silence is refusal: a proposal
 * nobody touches changes nothing. */
export interface ChatProposal {
  id: string
  kind: 'profile_patch' | 'vacancy_status' | 'vacancy_saved'
  status: 'pending' | 'accepted' | 'rejected'
  arguments: {
    op?: 'add_skill' | 'add_achievement' | 'set_field'
    value?: string
    company?: string
    field?: string
    rationale?: string
    vacancy_id?: string
    status?: string
    saved?: boolean
  }
  current: Record<string, unknown> | null
  proposed: Record<string, unknown> | null
  rationale: string | null
  created_at: string
}

export interface ChatResponse {
  reply: string
  messages: ChatMessage[]
  proposals: ChatProposal[]
}

/** The owner's dashboard, /api/admin/*. The one place the cabinet shows money:
 * users never see it, the owner pays for the key. */

export type AdminPlan = 'free' | 'paid' | 'admin'

export interface AdminUser {
  id: string
  email: string | null
  name: string | null
  telegram_name: string | null
  is_admin: boolean
  is_active: boolean
  email_verified: boolean
  plan: AdminPlan
  paid_until: string | null
  created_at: string
  /** Quota windows as the user sees them; null when the window is off. */
  day_pct: number | null
  week_pct: number | null
  has_resume: boolean
  vacancies: number
  evaluations: number
  /** Lifetime, from before the call journal too. */
  spent_rub: number
  spent_30d_rub: number
  last_call_at: string | null
}

export interface AdminUsers {
  total: number
  items: AdminUser[]
}

export type AdminUserSort = 'created' | 'spent' | 'last_call'

export interface SpendTotals {
  calls: number
  errors: number
  cache_hit: number
  cache_miss: number
  output: number
  usd: number
  /** The same calls at the off-peak price — what quotas are charged. */
  offpeak_usd: number
  rub: number
}

export interface SpendDay extends SpendTotals {
  /** A Moscow date, YYYY-MM-DD. */
  day: string
}

export interface SpendGroup extends SpendTotals {
  key: string
}

export interface SpendUser extends SpendTotals {
  user_id: string | null
  label: string
}

export interface AdminSpend {
  days: number
  since: string
  totals: SpendTotals
  daily: SpendDay[]
  by_step: SpendGroup[]
  by_model: SpendGroup[]
  top_users: SpendUser[]
  /** When the journal starts; earlier spend is only in `lifetime_rub`. */
  journal_since: string | null
  lifetime_rub: number
}

export type FunnelStage =
  'signed_up' | 'can_log_in' | 'resume' | 'scan' | 'evaluation' | 'applied'
export type FunnelExtra = 'triage' | 'saved' | 'artifact' | 'paid'
export type FunnelChannel = 'all' | 'email' | 'telegram'

export interface FunnelCohort {
  /** The Monday of the Moscow week the accounts signed up in, YYYY-MM-DD. */
  week: string
  /** Chained counts, one per stage. */
  stages: number[]
}

/** A cohort funnel: the period picks who signed up, the counts are what
 * they ever did. Each stage requires the ones before it. */
export interface AdminFunnel {
  /** 0 — every account ever. */
  days: number
  since: string | null
  stages: FunnelStage[]
  counts: number[]
  extras: Record<FunnelExtra, number>
  cohorts: FunnelCohort[]
}

/** Pool numbers of a source or a part of one. `active` — open and recent
 * enough for a scan to hand out. */
export interface SourceStats {
  postings: number
  active: number
  last_seen_at: string | null
}

export interface AdminAggregator extends SourceStats {
  source: string
  label: string
  fetched_at: string | null
}

/** A company name as the source wrote it, and what users see instead. */
export interface AdminCompanyName extends SourceStats {
  name: string
  display_name: string | null
}

export type ChannelGroup = 'core' | 'adjacent' | 'special'

export interface AdminChannel extends AdminCompanyName {
  key: string
  /** null — only in the pool, read by a scan that named it. */
  group: ChannelGroup | null
  hidden: boolean
}

export interface AdminBoard extends SourceStats {
  key: string
  label: string
  group: string
  hidden: boolean
  names: AdminCompanyName[]
}

export interface AdminSources {
  aggregators: AdminAggregator[]
  channels: AdminChannel[]
  boards: AdminBoard[]
}

export type SwitchableSource = 'telegram' | 'company'
