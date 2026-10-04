/** Дашборд владельца: страницы читают параметры из адреса и честно говорят,
 * где начинается журнал вызовов.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AdminFunnel, AdminSpend, AdminUsers, SpendTotals } from '../api/types'

const mocks = vi.hoisted(() => ({
  usersQuery: vi.fn(),
  spendDays: vi.fn(),
  funnelArgs: vi.fn(),
  funnel: null as AdminFunnel | null,
  users: null as AdminUsers | null,
  spend: null as AdminSpend | null,
}))

vi.mock('../api/hooks', () => ({
  useAdminUsers: (query: unknown) => {
    mocks.usersQuery(query)
    return { data: mocks.users, error: null, isLoading: false, isValidating: false }
  },
  useAdminFunnel: (days: number, channel: string) => {
    mocks.funnelArgs(days, channel)
    return { data: mocks.funnel, error: null, isLoading: false, isValidating: false }
  },
  useAdminSpend: (days: number) => {
    mocks.spendDays(days)
    return { data: mocks.spend, error: null, isLoading: false, isValidating: false }
  },
}))

const { AdminUsersPage } = await import('./AdminUsersPage')
const { AdminSpendPage } = await import('./AdminSpendPage')
const { AdminFunnelPage } = await import('./AdminFunnelPage')

const zero: SpendTotals = {
  calls: 0,
  errors: 0,
  cache_hit: 0,
  cache_miss: 0,
  output: 0,
  usd: 0,
  offpeak_usd: 0,
  rub: 0,
}

function at(path: string, page: React.ReactNode) {
  return render(<MemoryRouter initialEntries={[path]}>{page}</MemoryRouter>)
}

beforeEach(() => {
  mocks.usersQuery.mockClear()
  mocks.spendDays.mockClear()
})

describe('AdminUsersPage', () => {
  beforeEach(() => {
    mocks.users = {
      total: 1,
      items: [
        {
          id: 'u1',
          email: 'anna@example.com',
          name: 'Анна',
          telegram_name: null,
          is_admin: false,
          is_active: true,
          email_verified: false,
          plan: 'free',
          paid_until: null,
          created_at: '2026-10-01T09:00:00Z',
          day_pct: 100,
          week_pct: 40,
          has_resume: true,
          vacancies: 12,
          evaluations: 3,
          spent_rub: 4.5,
          spent_30d_rub: 0.512,
          last_call_at: null,
        },
      ],
    }
  })

  it('reads search, sort and page from the address', () => {
    at('/admin/users?q=anna&sort=spent&page=2', <AdminUsersPage />)
    expect(mocks.usersQuery).toHaveBeenLastCalledWith({
      q: 'anna',
      sort: 'spent',
      offset: 100,
      limit: 50,
    })
  })

  it('a new sort starts from the first page', () => {
    at('/admin/users?page=3', <AdminUsersPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Недавно активные' }))
    expect(mocks.usersQuery).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort: 'last_call', offset: 0 }),
    )
  })

  it('shows the account with its flags and an exhausted window', () => {
    at('/admin/users', <AdminUsersPage />)
    expect(screen.getByText('Анна')).toBeTruthy()
    expect(screen.getByText('anna@example.com')).toBeTruthy()
    expect(screen.getByText('почта не подтверждена')).toBeTruthy()
    expect(screen.getByTitle('лимит исчерпан').textContent).toBe('100%')
  })
})

describe('AdminSpendPage', () => {
  beforeEach(() => {
    mocks.spend = {
      days: 7,
      since: '2026-09-28T00:00:00+03:00',
      totals: {
        ...zero,
        calls: 4,
        cache_hit: 300,
        cache_miss: 700,
        output: 200,
        usd: 0.04,
        offpeak_usd: 0.03,
        rub: 3.34,
      },
      daily: Array.from({ length: 7 }, (_, i) => ({
        ...zero,
        day: `2026-10-0${i + 1}`,
        ...(i === 6 ? { calls: 4, usd: 0.04, rub: 3.34 } : {}),
      })),
      by_step: [{ ...zero, key: 'evaluate', calls: 4, rub: 3.34 }],
      by_model: [{ ...zero, key: 'deepseek-flash', calls: 4, rub: 3.34 }],
      top_users: [
        { ...zero, user_id: null, label: 'удалённая учётка', calls: 4, rub: 3.34 },
      ],
      journal_since: '2026-10-04T08:00:00Z',
      lifetime_rub: 120,
    }
  })

  it('takes the period from the address and falls back to 30 days', () => {
    at('/admin/spend?days=7', <AdminSpendPage />)
    expect(mocks.spendDays).toHaveBeenLastCalledWith(7)
    at('/admin/spend?days=13', <AdminSpendPage />)
    expect(mocks.spendDays).toHaveBeenLastCalledWith(30)
  })

  it('says the journal starts inside the period', () => {
    at('/admin/spend?days=7', <AdminSpendPage />)
    expect(screen.getByText(/Журнал вызовов ведётся с/)).toBeTruthy()
  })

  it('names steps in words and shows a tooltip for a day', () => {
    at('/admin/spend?days=7', <AdminSpendPage />)
    expect(screen.getByText('Оценка вакансии')).toBeTruthy()
    fireEvent.mouseEnter(screen.getByRole('button', { name: /7 октября/ }))
    expect(screen.getByRole('status').textContent).toMatch(/вызовов 4/)
  })

  it('an empty period says so instead of drawing a flat chart', () => {
    mocks.spend = {
      ...mocks.spend!,
      totals: zero,
      by_step: [],
      by_model: [],
      top_users: [],
    }
    at('/admin/spend', <AdminSpendPage />)
    expect(screen.getByText('За этот период вызовов не было')).toBeTruthy()
  })
})

describe('AdminFunnelPage', () => {
  beforeEach(() => {
    mocks.funnel = {
      days: 30,
      since: '2026-09-05T00:00:00+03:00',
      stages: ['signed_up', 'can_log_in', 'resume', 'scan', 'evaluation', 'applied'],
      counts: [40, 30, 20, 20, 8, 2],
      extras: { triage: 5, saved: 10, artifact: 3, paid: 1 },
      cohorts: [
        { week: '2026-09-28', stages: [10, 8, 5, 5, 2, 0] },
        { week: '2026-09-21', stages: [30, 22, 15, 15, 6, 2] },
      ],
    }
  })

  it('reads period and channel from the address', () => {
    at('/admin/funnel?days=0&channel=telegram', <AdminFunnelPage />)
    expect(mocks.funnelArgs).toHaveBeenLastCalledWith(0, 'telegram')
    at('/admin/funnel?days=13&channel=fax', <AdminFunnelPage />)
    expect(mocks.funnelArgs).toHaveBeenLastCalledWith(30, 'all')
  })

  it('writes the loss between stages and skips it where nobody dropped', () => {
    at('/admin/funnel', <AdminFunnelPage />)
    expect(screen.getByText('−10 · не дошли 25%')).toBeTruthy()
    expect(screen.getByText('−12 · не дошли 60%')).toBeTruthy()
    // resume → scan: 20 and 20, no loss line.
    expect(screen.getAllByText(/не дошли/)).toHaveLength(4)
  })

  it('shows weekly cohorts as shares of each week', () => {
    at('/admin/funnel', <AdminFunnelPage />)
    expect(screen.getByTitle('6 из 30').textContent).toBe('20%')
  })

  it('an empty cohort says so', () => {
    mocks.funnel = { ...mocks.funnel!, counts: [0, 0, 0, 0, 0, 0], cohorts: [] }
    at('/admin/funnel', <AdminFunnelPage />)
    expect(screen.getByText('За этот период никто не зарегистрировался')).toBeTruthy()
  })
})
