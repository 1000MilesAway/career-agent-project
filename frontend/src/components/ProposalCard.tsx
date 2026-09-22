/** One change the chat prepared, waiting for a yes or a no.
 *
 * The rule it serves is the product's, not the model's: the agent proposes,
 * the user accepts, and nothing reaches the profile without a click. The same
 * shape as `SearchProfileDiff` — say what changes, in the user's words, then
 * two buttons — because the user has already learned to read that one.
 *
 * Closing the page without deciding changes nothing. Silence is refusal.
 */

import { useState } from 'react'
import type { ChatProposal } from '../api/types'

/** What the change is called on screen. The model's `op` is an implementation
 *  detail; "Новый навык" is what the user is actually being asked about. */
const PATCH_TITLES: Record<string, string> = {
  add_skill: 'Новый навык',
  add_achievement: 'Новая строчка в опыте',
  set_field: 'Правка поля',
}

const FIELD_TITLES: Record<string, string> = {
  target_role: 'Желаемая должность',
  seniority: 'Уровень',
  field: 'Сфера',
  years_experience: 'Лет опыта',
}

const STATUS_TITLES: Record<string, string> = {
  none: 'не решено',
  applied: 'откликнулся',
  responded: 'ответили',
  interview: 'интервью',
  offer: 'оффер',
  rejected: 'отказ',
  discarded: 'отложено',
}

function title(proposal: ChatProposal): string {
  if (proposal.kind === 'profile_patch') {
    const op = proposal.arguments.op ?? ''
    if (op === 'set_field') {
      const field = proposal.arguments.field ?? ''
      return FIELD_TITLES[field] ?? PATCH_TITLES.set_field
    }
    return PATCH_TITLES[op] ?? 'Правка резюме'
  }
  if (proposal.kind === 'vacancy_status') return 'Статус в трекере'
  return proposal.arguments.saved ? 'В избранное' : 'Убрать из избранного'
}

/** The change itself, in one line the user can check against what they said. */
function summary(proposal: ChatProposal): string {
  if (proposal.kind === 'vacancy_status') {
    const to = String(proposal.arguments.status ?? '')
    return STATUS_TITLES[to] ?? to
  }
  if (proposal.kind === 'vacancy_saved') {
    return proposal.rationale ?? ''
  }
  const value = proposal.arguments.value ?? ''
  const company = proposal.arguments.company
  return company ? `${value} — ${company}` : value
}

export function ProposalCard({
  proposal,
  onAccept,
  onReject,
}: {
  proposal: ChatProposal
  onAccept: (id: string) => Promise<void>
  onReject: (id: string) => Promise<void>
}) {
  const [busy, setBusy] = useState<'accept' | 'reject' | null>(null)

  async function decide(what: 'accept' | 'reject') {
    setBusy(what)
    try {
      await (what === 'accept' ? onAccept : onReject)(proposal.id)
    } finally {
      setBusy(null)
    }
  }

  return (
    <article className="proposal">
      <header className="proposal__head">
        <span className="proposal__kind">{title(proposal)}</span>
      </header>

      <p className="proposal__value">{summary(proposal)}</p>

      {/* Why the model thinks this follows from what was said. It is the
          user's check against an invention: a rationale that does not match
          what they told the chat is the signal to reject. */}
      {proposal.rationale && proposal.kind === 'profile_patch' ? (
        <p className="proposal__why">{proposal.rationale}</p>
      ) : null}

      <div className="row">
        <button
          className="btn btn--small"
          onClick={() => decide('accept')}
          disabled={busy !== null}
        >
          {busy === 'accept' ? 'Применяю…' : 'Добавить'}
        </button>
        <button
          className="btn btn--ghost btn--small"
          onClick={() => decide('reject')}
          disabled={busy !== null}
        >
          {busy === 'reject' ? '…' : 'Не надо'}
        </button>
      </div>
    </article>
  )
}
