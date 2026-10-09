/** «Оплата»: тарифы, кнопка оплаты и возврат со страницы Platega. */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Billing } from '../api/types'

const mocks = vi.hoisted(() => ({
  billing: null as Billing | null,
  startCheckout: vi.fn(() => Promise.resolve({ url: 'https://pay.platega.io/?id=1' })),
  syncBilling: vi.fn(),
  mutate: vi.fn(() => Promise.resolve()),
  info: vi.fn(),
  error: vi.fn(),
}))

vi.mock('../api/hooks', () => ({
  BILLING_KEY: '/api/billing',
  STATE_KEY: '/api/state',
  useBilling: () => ({ data: mocks.billing }),
  startCheckout: mocks.startCheckout,
  syncBilling: mocks.syncBilling,
}))
vi.mock('swr', () => ({ useSWRConfig: () => ({ mutate: mocks.mutate }) }))
vi.mock('./toast', () => ({ useToast: () => ({ info: mocks.info, error: mocks.error }) }))

const { BillingDialog, usePaymentReturn } = await import('./BillingDialog')

const FREE: Billing = {
  enabled: true,
  price_rub: 400,
  days: 30,
  plan: 'free',
  paid_until: null,
  pro_vs_free: 2.5,
}

beforeAll(() => {
  // jsdom не реализует showModal, а компонент его вызывает.
  HTMLDialogElement.prototype.showModal = vi.fn()
  HTMLDialogElement.prototype.close = vi.fn()
})

beforeEach(() => {
  vi.clearAllMocks()
  mocks.billing = FREE
})

const dialog = () =>
  render(
    <MemoryRouter>
      <BillingDialog open onClose={() => {}} />
    </MemoryRouter>,
  )

describe('BillingDialog', () => {
  it('показывает оба тарифа и цену Pro', () => {
    dialog()
    expect(screen.getByText('Бесплатный')).toBeTruthy()
    expect(screen.getByText('400 ₽')).toBeTruthy()
    expect(screen.getByText('Лимит в 2,5 раза больше')).toBeTruthy()
    expect(screen.getByText('Ваш тариф')).toBeTruthy()
  })

  it('кнопка ведёт на страницу оплаты', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    dialog()
    fireEvent.click(screen.getByRole('button', { name: 'Оплатить 400 ₽', hidden: true }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://pay.platega.io/?id=1'))
    vi.unstubAllGlobals()
  })

  it('без ключей на сервере кнопка выключена', () => {
    mocks.billing = { ...FREE, enabled: false }
    dialog()
    const button = screen.getByRole('button', {
      name: 'Оплата скоро появится',
      hidden: true,
    })
    expect((button as HTMLButtonElement).disabled).toBe(true)
  })

  it('с действующим Pro — срок и продление', () => {
    mocks.billing = { ...FREE, plan: 'paid', paid_until: '2026-11-08T12:00:00Z' }
    dialog()
    expect(screen.getByText('Ваш тариф до 8 ноября')).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Продлить на месяц — 400 ₽', hidden: true }),
    ).toBeTruthy()
  })
})

function Returner({ onOpen }: { onOpen: () => void }) {
  usePaymentReturn(onOpen)
  const { search } = useLocation()
  return <p>search:{search}</p>
}

describe('usePaymentReturn', () => {
  it('после оплаты сверяет платёж, открывает тарифы и убирает параметр', async () => {
    mocks.syncBilling.mockResolvedValue({
      ...FREE,
      plan: 'paid',
      paid_until: '2026-11-08T12:00:00Z',
    })
    const onOpen = vi.fn()
    render(
      <MemoryRouter initialEntries={['/search?payment=done']}>
        <Returner onOpen={onOpen} />
      </MemoryRouter>,
    )
    await waitFor(() =>
      expect(mocks.info).toHaveBeenCalledWith('Pro подключён до 8 ноября'),
    )
    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(mocks.syncBilling).toHaveBeenCalledTimes(1)
    expect(screen.getByText('search:')).toBeTruthy()
  })

  it('неудачная оплата — ошибка без сверки', async () => {
    render(
      <MemoryRouter initialEntries={['/search?payment=failed']}>
        <Returner onOpen={() => {}} />
      </MemoryRouter>,
    )
    await waitFor(() => expect(mocks.error).toHaveBeenCalled())
    expect(mocks.syncBilling).not.toHaveBeenCalled()
  })
})
