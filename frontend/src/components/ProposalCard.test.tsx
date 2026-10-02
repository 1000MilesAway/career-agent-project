/** Правило подтверждений на разметке, а не на намерениях.
 *
 * Агент предлагает, пользователь принимает. Карточка обязана показать, что
 * именно изменится, и не должна менять ничего сама — здесь это проверяется
 * тем, что без нажатия наружу не уходит ни одного вызова.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ChatProposal } from '../api/types'
import { ProposalCard } from './ProposalCard'

const skill: ChatProposal = {
  id: 'p1',
  kind: 'profile_patch',
  status: 'pending',
  arguments: {
    op: 'add_skill',
    value: 'Kubernetes',
    rationale: 'Вы сказали, что год настраивали кластеры',
  },
  current: null,
  proposed: null,
  rationale: 'Вы сказали, что год настраивали кластеры',
  created_at: '2026-09-22T12:00:00Z',
}

describe('ProposalCard', () => {
  it('показывает, что именно добавится', () => {
    render(<ProposalCard proposal={skill} onAccept={vi.fn()} onReject={vi.fn()} />)

    expect(screen.getByText('Новый навык')).toBeTruthy()
    expect(screen.getByText('Kubernetes')).toBeTruthy()
  })

  it('показывает обоснование — по нему пользователь ловит выдумку', () => {
    render(<ProposalCard proposal={skill} onAccept={vi.fn()} onReject={vi.fn()} />)

    expect(screen.getByText(/год настраивали кластеры/)).toBeTruthy()
  })

  it('достижение показано вместе с местом работы', () => {
    render(
      <ProposalCard
        proposal={{
          ...skill,
          arguments: {
            op: 'add_achievement',
            value: 'Настройка кластеров Kubernetes',
            company: 'Авито',
          },
        }}
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />,
    )

    expect(screen.getByText(/Настройка кластеров Kubernetes — Авито/)).toBeTruthy()
  })

  it('без нажатия ничего не применяется', () => {
    const onAccept = vi.fn()
    const onReject = vi.fn()
    render(<ProposalCard proposal={skill} onAccept={onAccept} onReject={onReject} />)

    expect(onAccept).not.toHaveBeenCalled()
    expect(onReject).not.toHaveBeenCalled()
  })

  it('«Добавить» применяет именно это предложение', () => {
    const onAccept = vi.fn().mockResolvedValue(undefined)
    render(<ProposalCard proposal={skill} onAccept={onAccept} onReject={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Добавить' }))

    expect(onAccept).toHaveBeenCalledWith('p1')
  })

  it('«Не надо» отклоняет и ничего не пишет', () => {
    const onAccept = vi.fn()
    const onReject = vi.fn().mockResolvedValue(undefined)
    render(<ProposalCard proposal={skill} onAccept={onAccept} onReject={onReject} />)

    fireEvent.click(screen.getByRole('button', { name: 'Не надо' }))

    expect(onReject).toHaveBeenCalledWith('p1')
    expect(onAccept).not.toHaveBeenCalled()
  })

  it('статус трекера показан словом, а не кодом', () => {
    render(
      <ProposalCard
        proposal={{
          ...skill,
          kind: 'vacancy_status',
          arguments: { vacancy_id: 'v1', status: 'applied' },
          rationale: 'Python-разработчик → Откликнулся',
        }}
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />,
    )

    expect(screen.getByText('Откликнулся')).toBeTruthy()
  })
})
