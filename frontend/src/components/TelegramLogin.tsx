/** Telegram Login Widget: the button, and linking Telegram to the account.
 *
 * The widget is Telegram's own iframe, drawn by a script from telegram.org —
 * there is no other way to get a signed "this is Telegram user N" without
 * running a bot. The signature is checked on the server; nothing the widget
 * hands the page is trusted here.
 *
 * It only renders on the domain set for the bot in @BotFather (/setdomain).
 * On localhost or the bare IP the iframe says "Bot domain invalid" — expected,
 * not a bug.
 */

import { useEffect, useEffectEvent, useRef, useTransition } from 'react'
import useSWR, { useSWRConfig } from 'swr'
import { api, fetcher } from '../api/client'
import { STATE_KEY } from '../api/hooks'
import { useToast } from './toast'

/** What the widget hands back: id, names, auth_date, hash and whatever else
 *  Telegram signs. Passed to the server as is — the signature covers every
 *  field, so dropping one would break it. */
export type TelegramAuthData = Record<string, string | number>

const CONFIG_KEY = '/api/auth/telegram'

/** Which bot the widget is for; null while loading or when it is switched off. */
export function useTelegramBot(): string | null {
  const { data } = useSWR<{ bot_username: string | null }>(CONFIG_KEY, fetcher, {
    revalidateOnFocus: false,
  })
  return data?.bot_username ?? null
}

// The widget calls back into a global by name. A counter rather than useId:
// useId yields ":r1:", which is not a valid identifier inside data-onauth.
let widgetSeq = 0

export function TelegramButton({
  bot,
  onAuth,
  size = 'large',
}: {
  bot: string
  onAuth: (data: TelegramAuthData) => void
  size?: 'large' | 'medium' | 'small'
}) {
  const host = useRef<HTMLDivElement>(null)
  // The latest handler without re-injecting the iframe on every render.
  const handleAuth = useEffectEvent((data: TelegramAuthData) => onAuth(data))

  useEffect(() => {
    const el = host.current
    if (!el) return
    const name = `__careerTelegramAuth${++widgetSeq}`
    const w = window as unknown as Record<string, unknown>
    w[name] = (data: TelegramAuthData) => handleAuth(data)

    const script = document.createElement('script')
    script.async = true
    script.src = 'https://telegram.org/js/telegram-widget.js?22'
    script.dataset.telegramLogin = bot
    script.dataset.size = size
    script.dataset.radius = '8'
    script.dataset.userpic = 'false'
    script.dataset.lang = 'ru'
    script.dataset.onauth = `${name}(user)`
    el.appendChild(script)

    return () => {
      delete w[name]
      el.replaceChildren()
    }
  }, [bot, size])

  return <div ref={host} className="tg-button" />
}

/** Sidebar block: link Telegram for login, or show what is linked. */
export function TelegramLink({ linkedAs }: { linkedAs: string | null }) {
  const bot = useTelegramBot()
  const toast = useToast()
  const { mutate } = useSWRConfig()
  const [isPending, startTransition] = useTransition()

  function run(action: () => Promise<unknown>, done: string) {
    startTransition(async () => {
      try {
        await action()
        await mutate(STATE_KEY)
        toast.info(done)
      } catch (e) {
        toast.error(e)
      }
    })
  }

  if (linkedAs) {
    return (
      <div className="side__telegram">
        <span className="side__telegram-name" title="Вход через Telegram включён">
          Telegram: {linkedAs}
        </span>
        <button
          className="btn btn--quiet btn--small"
          disabled={isPending}
          onClick={() =>
            run(
              () => api('/api/auth/telegram/link', { method: 'DELETE' }),
              'Telegram отвязан. Вход — по email и паролю.',
            )
          }
        >
          Отвязать
        </button>
      </div>
    )
  }

  if (!bot) return null
  return (
    <div className="side__telegram side__telegram--offer">
      <span className="side__telegram-name">Входить через Telegram:</span>
      <TelegramButton
        bot={bot}
        size="small"
        onAuth={(data) =>
          run(
            () => api('/api/auth/telegram/link', { method: 'POST', json: data }),
            'Telegram привязан — теперь можно входить через него.',
          )
        }
      />
    </div>
  )
}
