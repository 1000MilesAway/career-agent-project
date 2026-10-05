/** Data access for the cabinet.
 *
 * /api/state answers "what should the screen show right now" in one payload,
 * and the server was built that way deliberately (see app/api/state.py). SWR
 * gives every page the same cached copy, so three pages mean one request
 * rather than three, and any mutation revalidates it in one call.
 */

import useSWR, { mutate } from 'swr'
import { api, download, fetcher, fetchText } from './client'
import type {
  AdminFunnel,
  AdminSources,
  AdminSpend,
  AdminUserSort,
  AdminUsers,
  ApplyStepName,
  FunnelChannel,
  SwitchableSource,
  AppState,
  ArtifactResponse,
  ChatMessage,
  ChatProposal,
  ChatResponse,
  EvaluationResponse,
  FoundVacancy,
  Job,
  RecheckResult,
  ResumeProfile,
  ResumeResponse,
  ScanJob,
  TriageJob,
  SearchProfile,
  SearchProfileProposal,
} from './types'

export const STATE_KEY = '/api/state'
export const CHAT_KEY = '/api/chat'
export const PROPOSALS_KEY = '/api/chat/proposals'

export function useAppState(enabled = true) {
  return useSWR<AppState>(enabled ? STATE_KEY : null, fetcher, {
    // A scan writes rows the user is watching for; a stale list here reads as
    // "the scan found nothing".
    revalidateOnFocus: true,
    // Two pages mounting at once must not fire two identical requests.
    dedupingInterval: 2000,
  })
}

export interface ScanParams {
  /** The cabinet sends 'all'; single sources remain for the API. */
  source: 'all' | 'habr' | 'hh' | 'telegram' | 'geekjob' | 'company'
  salary_min?: number | null
  any_salary?: boolean
  remote_only?: boolean
  /** Drop employers whose IT accreditation is explicitly not confirmed. */
  accredited_only?: boolean
  /** On-site cities; [] — any city. Remote postings pass regardless. */
  cities?: string[]
  /** Also read employers abroad hiring Russian speakers. */
  include_global?: boolean
}

/** The latest scan, polled while it runs.
 *
 * A scan takes up to a minute, so it is a job on the server rather than a
 * request the page holds open: awaiting it inside a transition kept every
 * other transition — react-router's navigation among them — waiting too, and
 * the result died with the page if the user left it. The row lives on the
 * server, so the progress survives leaving the page and reloading the tab.
 */
export const SCAN_JOB_KEY = '/api/jobs?kind=scan&limit=1'

const JOB_POLL_MS = 2000

export const isActive = (job: Pick<Job, 'status'> | null | undefined): boolean =>
  job?.status === 'queued' || job?.status === 'running'

function useLatestJob<J extends Job>(key: string, enabled: boolean) {
  const swr = useSWR<J[]>(enabled ? key : null, fetcher, {
    // Polled only while one runs: an idle cabinet asks nothing.
    refreshInterval: (rows) => (isActive(rows?.[0]) ? JOB_POLL_MS : 0),
    dedupingInterval: 1000,
  })
  return { ...swr, job: swr.data?.[0] ?? null }
}

export const useScanJob = (enabled = true) => useLatestJob<ScanJob>(SCAN_JOB_KEY, enabled)

/** The background re-read of listings a scan answered from a stale pool.
 * Queued by the worker, not by the cabinet; same result shape as a scan. */
export const REFRESH_JOB_KEY = '/api/jobs?kind=refresh&limit=1'

export const useRefreshJob = (enabled = true) =>
  useLatestJob<ScanJob>(REFRESH_JOB_KEY, enabled)

/** The latest «умный отбор». A job for the same reason a scan is one: a model
 * call awaited inside a transition held the cabinet's navigation with it. */
export const TRIAGE_JOB_KEY = '/api/jobs?kind=triage&limit=1'

export const useTriageJob = (enabled = true) =>
  useLatestJob<TriageJob>(TRIAGE_JOB_KEY, enabled)

/** When the last finished triage's verdicts reached the list.
 *
 * Set by the watcher after the state is refetched, not when the job turns
 * done: the job finishes a moment before its verdicts arrive, and the search
 * page retakes its order on this mark — too early, and it would sort the old
 * rows. Local to the browser: SWR's cache with no fetcher.
 */
export const TRIAGE_SETTLED_KEY = 'local:triage-settled'

export function useTriageSettled(): string | null {
  return useSWR<string | null>(TRIAGE_SETTLED_KEY, null).data ?? null
}

export const markTriageSettled = (finishedAt: string | null) =>
  mutate(TRIAGE_SETTLED_KEY, finishedAt, { revalidate: false })

/** Queue a scan. A scan already running is returned instead of a second one. */
export const startScan = (params: ScanParams) =>
  api<ScanJob>('/api/jobs', { method: 'POST', json: { kind: 'scan', payload: params } })

/** «Скрыть» and back. Nothing is deleted: the row folds under its own toggle. */
export const setHidden = (vacancyId: string, hidden: boolean) =>
  api<unknown>(`/api/vacancies/${vacancyId}/hide`, {
    method: hidden ? 'POST' : 'DELETE',
  })

/** Ask the sources whether saved and shortlisted vacancies are still open.
 * Free: one page request each, no model. */
export const recheckVacancies = () =>
  api<RecheckResult>('/api/vacancies/recheck', { method: 'POST' })

/** Queue a triage. `ids` limits the paid call to the rows the user's
 * conditions keep. One already running is returned instead of a second. */
export const startTriage = (ids?: string[]) =>
  api<TriageJob>('/api/jobs', {
    method: 'POST',
    json: { kind: 'triage', payload: { ids: ids ?? null } },
  })

export const clearFound = () =>
  api<{ deleted: number }>('/api/vacancies/clear', { method: 'POST' })

export const evaluateVacancy = (vacancyId: string) =>
  api<EvaluationResponse>(`/api/evaluations/${vacancyId}`, { method: 'POST' })

export const buyArtifact = (vacancyId: string, step: ApplyStepName) =>
  api<ArtifactResponse>(`/api/artifacts/${vacancyId}/${step}`, { method: 'POST' })

export const setSaved = (vacancyId: string, saved: boolean) =>
  api<unknown>(`/api/vacancies/${vacancyId}/save`, {
    method: saved ? 'POST' : 'DELETE',
  })

/** A change to one row, drawn on the click rather than after /api/state comes
 *  back: the write is one UPDATE, while the state carries every found row —
 *  hundreds of kilobytes after a scan. The list still reloads behind it, and a
 *  failed write puts the row back; the caller reports the error. */
function changeRow(
  vacancyId: string,
  change: Partial<FoundVacancy>,
  write: () => Promise<unknown>,
) {
  return mutate<AppState>(
    STATE_KEY,
    async (current) => {
      await write()
      return current
    },
    {
      optimisticData: (current) => ({
        ...current!,
        found: current!.found.map((row) =>
          row.id === vacancyId ? { ...row, ...change } : row,
        ),
      }),
      rollbackOnError: true,
      populateCache: false,
      revalidate: true,
    },
  )
}

export const saveVacancy = (vacancyId: string, saved: boolean) =>
  changeRow(vacancyId, { saved }, () => setSaved(vacancyId, saved))

export const hideVacancy = (vacancyId: string, hidden: boolean) =>
  changeRow(vacancyId, { hidden }, () => setHidden(vacancyId, hidden))

export const moveVacancy = (vacancyId: string, status: string) =>
  changeRow(vacancyId, { status }, () => setStatus(vacancyId, status))

export const setStatus = (vacancyId: string, status: string) =>
  api<unknown>(`/api/vacancies/${vacancyId}/status`, {
    method: 'PUT',
    json: { status },
  })

/** Blank clears the name; the server trims it. */
export const updateProfile = (fields: { name: string }) =>
  api<unknown>('/api/auth/me', { method: 'PATCH', json: fields })

export function uploadResume(file: File): Promise<ResumeResponse> {
  const form = new FormData()
  form.append('file', file)
  return api<ResumeResponse>('/api/resumes', { method: 'POST', form })
}

/** Free: copies an earlier version forward as the newest one. */
export const restoreResume = (resumeId: string) =>
  api<ResumeResponse>(`/api/resumes/${resumeId}/restore`, { method: 'POST' })

/** Free: the user's own correction of the parsed CV, saved as a new version.
 * Keys left out keep their stored value. */
export const editResumeProfile = (profile: ResumeProfile) =>
  api<ResumeResponse>('/api/resumes/active/profile', { method: 'PUT', json: profile })

export const proposeSearchProfile = () =>
  api<SearchProfileProposal>('/api/resumes/search-profile/propose', { method: 'POST' })

/** A PUT of the whole object: what the user saw in the diff is what is saved. */
export const acceptSearchProfile = (profile: SearchProfile) =>
  api<ResumeResponse>('/api/resumes/search-profile', { method: 'PUT', json: profile })

/** Downloads. Free — they render JSON that was already paid for. */

export type ExportFormat = 'pdf' | 'docx' | 'md'

export interface ResumeStyle {
  id: string
  name: string
  description: string
}

export const STYLES_KEY = '/api/export/styles'

/** The presets never change while the app is open: fetch once. */
export function useResumeStyles(enabled = true) {
  return useSWR<ResumeStyle[]>(enabled ? STYLES_KEY : null, fetcher, {
    revalidateOnFocus: false,
    revalidateIfStale: false,
  })
}

/** Where a document is exported from, without the format: one of
 *  `/api/export/resume` or `/api/export/vacancy/{id}/tailor`. */
export const profileExportBase = '/api/export/resume'
export const tailoredExportBase = (vacancyId: string) =>
  `/api/export/vacancy/${vacancyId}/tailor`

const exportPath = (base: string, fmt: ExportFormat | 'html', style: string) =>
  `${base}.${fmt}?style=${encodeURIComponent(style)}`

export const downloadExport = (base: string, fmt: ExportFormat, style: string) =>
  download(exportPath(base, fmt, style), `resume.${fmt}`)

/** The preview is the very HTML the PDF is made from, keyed by style so
 *  flipping back to a seen preset is instant. */
export function useResumePreview(base: string | null, style: string) {
  return useSWR<string>(base ? exportPath(base, 'html', style) : null, fetchText, {
    revalidateOnFocus: false,
    keepPreviousData: true,
  })
}

/** The chat.
 *
 * Its own SWR keys rather than a slice of /api/state: the conversation grows
 * on its own schedule and a scan revalidating the whole screen must not drag
 * the message list with it.
 */

export function useChat(enabled = true) {
  return useSWR<ChatMessage[]>(enabled ? CHAT_KEY : null, fetcher, {
    // Nothing writes to the conversation except this tab.
    revalidateOnFocus: false,
  })
}

export function useProposals(enabled = true) {
  return useSWR<ChatProposal[]>(enabled ? PROPOSALS_KEY : null, fetcher, {
    revalidateOnFocus: false,
  })
}

export const sendMessage = (text: string) =>
  api<ChatResponse>('/api/chat', { method: 'POST', json: { text } })

export const acceptProposal = (id: string) =>
  api<ChatProposal>(`/api/chat/proposals/${id}/accept`, { method: 'POST' })

export const rejectProposal = (id: string) =>
  api<ChatProposal>(`/api/chat/proposals/${id}/reject`, { method: 'POST' })

export const clearChat = () =>
  api<{ messages: number; proposals: number }>('/api/chat/clear', { method: 'POST' })

/** The owner's dashboard.
 *
 * Its own keys, off /api/state: an aggregate over every account is not "what
 * this screen shows for me", and it must not be refetched on every scan.
 * keepPreviousData holds the table while the next page or period loads, so
 * paging does not flash a skeleton.
 */

export interface AdminUsersQuery {
  q: string
  sort: AdminUserSort
  offset: number
  limit: number
}

export const adminUsersKey = ({ q, sort, offset, limit }: AdminUsersQuery) =>
  `/api/admin/users?${new URLSearchParams({
    q,
    sort,
    offset: String(offset),
    limit: String(limit),
  })}`

export const useAdminUsers = (query: AdminUsersQuery) =>
  useSWR<AdminUsers>(adminUsersKey(query), fetcher, { keepPreviousData: true })

export const useAdminSpend = (days: number) =>
  useSWR<AdminSpend>(`/api/admin/spend?days=${days}`, fetcher, { keepPreviousData: true })

export const useAdminFunnel = (days: number, channel: FunnelChannel) =>
  useSWR<AdminFunnel>(`/api/admin/funnel?days=${days}&channel=${channel}`, fetcher, {
    keepPreviousData: true,
  })

export const ADMIN_SOURCES_KEY = '/api/admin/sources'

export const useAdminSources = () => useSWR<AdminSources>(ADMIN_SOURCES_KEY, fetcher)

/** Hide a channel or board from every user's results, or bring it back.
 *  The table flips at once and settles on the server's answer. */
export async function setSourceHidden(
  source: SwitchableSource,
  key: string,
  hidden: boolean,
) {
  const list = source === 'telegram' ? 'channels' : 'boards'
  await mutate<AdminSources>(
    ADMIN_SOURCES_KEY,
    async (current) => {
      await api('/api/admin/sources/hidden', {
        method: 'PUT',
        json: { source, key, hidden },
      })
      return current
    },
    {
      optimisticData: (current) => ({
        ...current!,
        [list]: current![list].map((item) =>
          item.key === key ? { ...item, hidden } : item,
        ),
      }),
      rollbackOnError: true,
      populateCache: false,
      revalidate: true,
    },
  )
}

/** Show a company under another name; empty — under its own. */
export async function setCompanyName(name: string, displayName: string) {
  await api('/api/admin/sources/name', {
    method: 'PUT',
    json: { name, display_name: displayName.trim() || null },
  })
  await mutate(ADMIN_SOURCES_KEY)
}
