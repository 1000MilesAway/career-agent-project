/** Data access for the cabinet.
 *
 * /api/state answers "what should the screen show right now" in one payload,
 * and the server was built that way deliberately (see app/api/state.py). SWR
 * gives every page the same cached copy, so three pages mean one request
 * rather than three, and any mutation revalidates it in one call.
 */

import useSWR from 'swr'
import { api, download, fetcher } from './client'
import type {
  ApplyStepName,
  AppState,
  ArtifactResponse,
  ChatMessage,
  ChatProposal,
  ChatResponse,
  EvaluationResponse,
  RecheckResult,
  ResumeResponse,
  ScanResult,
  SearchProfile,
  SearchProfileProposal,
  TriageResult,
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
  source: 'habr' | 'telegram' | 'geekjob' | 'company'
  pages: number
  salary_min?: number | null
  any_salary?: boolean
  remote_only?: boolean
  groups?: string[]
}

export const scanVacancies = (params: ScanParams) =>
  api<ScanResult>('/api/vacancies/scan', { method: 'POST', json: params })

/** Ask the sources whether saved and shortlisted vacancies are still open.
 * Free: one page request each, no model. */
export const recheckVacancies = () =>
  api<RecheckResult>('/api/vacancies/recheck', { method: 'POST' })

export const runTriage = () =>
  api<TriageResult>('/api/vacancies/triage', { method: 'POST' })

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

export const setStatus = (vacancyId: string, status: string) =>
  api<unknown>(`/api/vacancies/${vacancyId}/status`, {
    method: 'PUT',
    json: { status },
  })

export function uploadResume(file: File): Promise<ResumeResponse> {
  const form = new FormData()
  form.append('file', file)
  return api<ResumeResponse>('/api/resumes', { method: 'POST', form })
}

export const proposeSearchProfile = () =>
  api<SearchProfileProposal>('/api/resumes/search-profile/propose', { method: 'POST' })

/** A PUT of the whole object: what the user saw in the diff is what is saved. */
export const acceptSearchProfile = (profile: SearchProfile) =>
  api<ResumeResponse>('/api/resumes/search-profile', { method: 'PUT', json: profile })

/** Downloads. Free — they render JSON that was already paid for. */

export type ExportFormat = 'pdf' | 'docx'

export const downloadProfile = (fmt: ExportFormat) =>
  download(`/api/export/resume.${fmt}`, `resume.${fmt}`)

export const downloadTailored = (vacancyId: string, fmt: ExportFormat) =>
  download(`/api/export/vacancy/${vacancyId}/tailor.${fmt}`, `resume.${fmt}`)

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
