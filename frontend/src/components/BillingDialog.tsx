/** «Оплата»: the two plans and the way to Pro.
 *
 * A dialog from the user menu rather than a page: it is opened to pay or to
 * see until when Pro runs, and neither is worth a place among the sections
 * people work in. Paying happens on Platega's page in a new tab, so the cabinet
 * stays where it was; that tab comes back to /search?payment=…, and
 * `usePaymentReturn` reopens this dialog there.
 *
 * <dialog> with the account dialog's frame (`account.css`).
 */

import { useEffect, useRef, useTransition } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useSWRConfig } from 'swr'
import {
  BILLING_KEY,
  STATE_KEY,
  startCheckout,
  syncBilling,
  useBilling,
} from '../api/hooks'
import type { Billing } from '../api/types'
import { CloseIcon } from './icons'
import { useToast } from './toast'
import './account.css'
import './billing.css'

const until = (iso: string) =>
  new Date(iso).toLocaleDateString('ru-RU', {
    timeZone: 'Europe/Moscow',
    day: 'numeric',
    month: 'long',
  })

export function BillingDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const { data } = useBilling(open)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      className="account billing"
      aria-label="Оплата"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target === ref.current) onClose()
      }}
    >
      <header className="account__head">
        <h2 className="account__title">Тарифы</h2>
        <button
          type="button"
          className="btn btn--quiet btn--small btn--icon"
          onClick={onClose}
          aria-label="Закрыть"
          title="Закрыть (Esc)"
        >
          <CloseIcon />
        </button>
      </header>
      {data ? <Plans billing={data} /> : <div className="account__body billing__wait" />}
    </dialog>
  )
}

function Plans({ billing }: { billing: Billing }) {
  const paid = billing.plan === 'paid'
  return (
    <div className="account__body">
      <div className="billing__plans">
        <section className={paid ? 'plan' : 'plan plan--current'}>
          <h3 className="plan__name">Бесплатный</h3>
          <p className="plan__price">
            <b>0 ₽</b>
          </p>
          <ul className="plan__perks">
            <li>Поиск по всем источникам</li>
            <li>Оценки и документы в пределах дневного лимита</li>
          </ul>
          {paid ? null : <p className="plan__badge">Ваш тариф</p>}
        </section>

        <section className={paid ? 'plan plan--pro plan--current' : 'plan plan--pro'}>
          <h3 className="plan__name">Pro</h3>
          <p className="plan__price">
            <b>{billing.price_rub} ₽</b> / месяц
          </p>
          <ul className="plan__perks">
            <li>Расширенные лимиты</li>
            <li>Продвинутые функции</li>
          </ul>
          {paid && billing.paid_until ? (
            <p className="plan__badge">Ваш тариф до {until(billing.paid_until)}</p>
          ) : null}
        </section>
      </div>

      <PayButton billing={billing} />
    </div>
  )
}

function PayButton({ billing }: { billing: Billing }) {
  const [isPending, startTransition] = useTransition()
  const toast = useToast()

  if (!billing.enabled) {
    return (
      <button type="button" className="btn billing__pay" disabled>
        Оплата скоро появится
      </button>
    )
  }

  const label =
    billing.plan === 'paid'
      ? `Продлить на месяц — ${billing.price_rub} ₽`
      : `Оплатить ${billing.price_rub} ₽`

  function onPay() {
    // Opened in the click itself, before the checkout request: a window.open
    // after an await has lost the user gesture and is blocked as a popup. The
    // tab gets Platega's address once it arrives; a blocked tab falls back to
    // paying in this one.
    const tab = window.open('', '_blank')
    if (tab) tab.opener = null
    startTransition(async () => {
      try {
        const { url } = await startCheckout()
        if (tab) tab.location.href = url
        else window.location.assign(url)
      } catch (e) {
        tab?.close()
        toast.error(e)
      }
    })
  }

  return (
    <button type="button" className="btn billing__pay" onClick={onPay} disabled={isPending}>
      {isPending ? 'Открываю оплату…' : label}
    </button>
  )
}

/** Back from Platega's page with ?payment=done|failed: ask the server where
 *  the payment stands (the callback may lag behind the browser), say so, and
 *  drop the parameter so a reload does not ask again. */
export function usePaymentReturn(onOpen: () => void) {
  const [params, setParams] = useSearchParams()
  const outcome = params.get('payment')
  const { mutate } = useSWRConfig()
  const toast = useToast()
  // StrictMode runs effects twice in development; one return, one toast.
  const handled = useRef<string | null>(null)

  useEffect(() => {
    if (!outcome || handled.current === outcome) return
    handled.current = outcome
    const next = new URLSearchParams(params)
    next.delete('payment')
    setParams(next, { replace: true })
    onOpen()

    if (outcome === 'failed') {
      toast.error(new Error('Оплата не прошла. Можно попробовать ещё раз.'))
      return
    }
    void syncBilling()
      .then(async (billing) => {
        await mutate(BILLING_KEY, billing)
        await mutate(STATE_KEY)
        toast.info(
          billing.plan === 'paid' && billing.paid_until
            ? `Pro подключён до ${until(billing.paid_until)}`
            : 'Платёж обрабатывается: тариф обновится через минуту-другую',
        )
      })
      .catch((e: unknown) => toast.error(e))
  }, [outcome, params, setParams, onOpen, mutate, toast])
}
