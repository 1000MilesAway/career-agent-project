/** The CV profile as editable form text, and back.
 *
 * Directions and skills are kept as tags, the other lists as one item per
 * line; the server trims lines and drops blanks and
 * repeats, so nothing here has to.
 */

import type { ResumeProfile } from '../api/types'

type Job = NonNullable<ResumeProfile['experience']>[number]

export interface JobDraft {
  /** Stable across reorders, so React does not move typed text between blocks. */
  key: number
  /** Keys the form does not show, sent back untouched. */
  rest: Record<string, unknown>
  company: string
  role: string
  period: string
  location: string
  description: string
  achievements: string
}

export interface ProfileDraft {
  name: string
  target_role: string
  seniority: string
  years_experience: string
  field: string
  /** Where the person lives and can work on site; the search starts there. */
  city: string
  summary: string
  directions: string[]
  skills: string[]
  education: string
  languages: string
  experience: JobDraft[]
}

export const SENIORITY = ['intern', 'junior', 'middle', 'senior', 'lead'] as const

let nextKey = 0

const lines = (items?: string[] | null) => (items ?? []).join('\n')
const split = (text: string) => text.split('\n')

export function jobDraft(job?: Job): JobDraft {
  const { company, role, period, location, description, achievements, ...rest } =
    job ?? ({} as Job)
  return {
    key: nextKey++,
    rest,
    company: company ?? '',
    role: role ?? '',
    period: period ?? '',
    location: location ?? '',
    description: description ?? '',
    achievements: lines(achievements),
  }
}

export function toDraft(profile: ResumeProfile): ProfileDraft {
  return {
    name: profile.name ?? '',
    target_role: profile.target_role ?? '',
    seniority: profile.seniority ?? '',
    years_experience:
      profile.years_experience != null ? String(profile.years_experience) : '',
    field: profile.field ?? '',
    city: profile.preferences?.city ?? '',
    summary: profile.summary ?? '',
    directions: profile.directions ?? [],
    skills: profile.skills ?? [],
    education: lines(profile.education),
    languages: lines(profile.languages),
    experience: (profile.experience ?? []).map(jobDraft),
  }
}

/** Only the keys the form shows: the server keeps the rest as they were. */
export function fromDraft(draft: ProfileDraft): ResumeProfile {
  const years = Number.parseFloat(draft.years_experience.replace(',', '.'))
  return {
    name: draft.name,
    target_role: draft.target_role,
    seniority: draft.seniority || undefined,
    years_experience: Number.isFinite(years) ? years : undefined,
    field: draft.field,
    // The city alone: the server merges it into the stored preferences.
    preferences: { city: draft.city },
    summary: draft.summary,
    directions: draft.directions,
    skills: draft.skills,
    education: split(draft.education),
    languages: split(draft.languages),
    experience: draft.experience.map((job) => ({
      ...job.rest,
      company: job.company,
      role: job.role,
      period: job.period,
      location: job.location,
      description: job.description,
      achievements: split(job.achievements),
    })),
  }
}
