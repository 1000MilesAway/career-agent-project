/** Ручная правка резюме: что уходит на сервер и когда.
 *
 * Сохранение создаёт новую версию резюме, а оценки по прежней получают пометку
 * «прежнее резюме» — поэтому сохранение без правок не должно звать сервер.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ResumeProfile } from '../api/types'

const mocks = vi.hoisted(() => ({
  editResumeProfile: vi.fn((_profile: unknown) => Promise.resolve({ revision: 2 })),
  mutate: vi.fn(() => Promise.resolve()),
  info: vi.fn(),
}))

vi.mock('../api/hooks', () => ({
  STATE_KEY: '/api/state',
  CHAT_KEY: '/api/chat',
  editResumeProfile: mocks.editResumeProfile,
}))
vi.mock('swr', () => ({ useSWRConfig: () => ({ mutate: mocks.mutate }) }))
vi.mock('./toast', () => ({ useToast: () => ({ info: mocks.info, error: vi.fn() }) }))

const { ProfileEditor } = await import('./ProfileEditor')
const { fromDraft, toDraft } = await import('./profileDraft')

const profile: ResumeProfile = {
  name: 'Макс',
  target_role: 'Backend',
  seniority: 'middle',
  years_experience: 5,
  skills: ['Python', 'Go'],
  experience: [
    {
      company: 'Ромашка',
      role: 'Dev',
      period: '2020 — 2024',
      achievements: ['Ускорил API в 3 раза'],
    },
    { company: 'Лютик', role: 'Junior', period: '2018 — 2020' },
  ],
}

describe('profileDraft', () => {
  it('туда и обратно сохраняет профиль', () => {
    const back = fromDraft(toDraft(profile))
    expect(back.skills).toEqual(['Python', 'Go'])
    expect(back.years_experience).toBe(5)
    expect(back.experience?.[0].achievements).toEqual(['Ускорил API в 3 раза'])
  })

  it('незнакомые поля места работы уходят обратно как были', () => {
    const job = { company: 'А', role: 'Б', period: '', extra: 'x' }
    expect(fromDraft(toDraft({ experience: [job] })).experience?.[0]).toMatchObject({
      extra: 'x',
    })
  })

  it('лет опыта с запятой читается как число', () => {
    const draft = { ...toDraft(profile), years_experience: '3,5' }
    expect(fromDraft(draft).years_experience).toBe(3.5)
  })
})

describe('ProfileEditor', () => {
  it('без правок не зовёт сервер', () => {
    const onClose = vi.fn()
    render(<ProfileEditor profile={profile} onClose={onClose} />)
    expect(
      (screen.getByRole('button', { name: 'Сохранить' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }))
    expect(onClose).toHaveBeenCalled()
    expect(mocks.editResumeProfile).not.toHaveBeenCalled()
  })

  it('сохраняет правку, удаление места работы и обновляет состояние и чат', async () => {
    const onClose = vi.fn()
    render(<ProfileEditor profile={profile} onClose={onClose} />)

    fireEvent.change(screen.getByLabelText('Целевая должность'), {
      target: { value: 'Backend-разработчик' },
    })
    fireEvent.click(screen.getAllByRole('button', { name: 'Удалить место работы' })[1])
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    const sent = mocks.editResumeProfile.mock.calls[0][0] as ResumeProfile
    expect(sent.target_role).toBe('Backend-разработчик')
    expect(sent.experience?.map((j) => j.company)).toEqual(['Ромашка'])
    expect(mocks.mutate).toHaveBeenCalledWith('/api/state')
    expect(mocks.mutate).toHaveBeenCalledWith('/api/chat')
    expect(mocks.info).toHaveBeenCalledWith('Сохранено как версия 2')
  })

  it('порядок мест работы меняется кнопками', async () => {
    render(<ProfileEditor profile={profile} onClose={vi.fn()} />)
    fireEvent.click(screen.getAllByRole('button', { name: 'Ниже' })[0])
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(mocks.editResumeProfile).toHaveBeenCalled())
    const sent = mocks.editResumeProfile.mock.lastCall?.[0] as ResumeProfile
    expect(sent.experience?.map((j) => j.company)).toEqual(['Лютик', 'Ромашка'])
  })
})
