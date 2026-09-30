/** Telegram Login (OpenID Connect): the button, and linking Telegram to the account.
 *
 * Telegram's telegram-login.js opens its own popup and hands back an
 * id_token; the API checks its signature, audience and nonce, so nothing the
 * popup returns is trusted here. The button is ours rather than Telegram's
 * drawn one, to sit in the cabinet's own style.
 *
 * The popup only works for the URLs listed in the bot's Allowed URLs
 * (@BotFather -> bot -> Login Widget). On localhost or the bare IP Telegram
 * refuses — expected, not a bug.
 */

import { useEffect, useTransition } from 'react'
import useSWR, { useSWRConfig } from 'swr'
import { api, fetcher } from '../api/client'
import { STATE_KEY } from '../api/hooks'
import { TelegramIcon } from './icons'
import { useToast } from './toast'

interface TelegramConfig {
  client_id: number | null
  nonce: string | null
}

interface TelegramLoginResult {
  id_token?: string
  error?: string
}

interface TelegramLoginApi {
  auth(
    options: { client_id: number; nonce?: string; lang?: string },
    callback: (result: TelegramLoginResult) => void,
  ): void
}

declare global {
  interface Window {
    Telegram?: { Login?: TelegramLoginApi }
  }
}

const CONFIG_KEY = '/api/auth/telegram'
const SCRIPT_SRC = 'https://oauth.telegram.org/js/telegram-login.js?6'

/** Client id and a fresh nonce; null while loading or when Telegram login is off.
 *
 * The server's nonce lives an hour, so a page left open refetches it every
 * half hour — otherwise the first click after lunch fails for no visible reason.
 */
export function useTelegramConfig(): { client_id: number; nonce: string } | null {
  const { data } = useSWR<TelegramConfig>(CONFIG_KEY, fetcher, {
    revalidateOnFocus: false,
    refreshInterval: 30 * 60 * 1000,
  })
  if (!data?.client_id || !data.nonce) return null
  return { client_id: data.client_id, nonce: data.nonce }
}

// One script per page, however many buttons: it registers a global and a
// document-wide click listener, and a second copy would double both.
let scriptLoading: Promise<void> | null = null

function loadScript(): Promise<void> {
  if (window.Telegram?.Login) return Promise.resolve()
  scriptLoading ??= new Promise<void>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = SCRIPT_SRC
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => {
      // Let the next click try again rather than caching the failure.
      scriptLoading = null
      script.remove()
      reject(new Error('Не удалось загрузить вход через Telegram'))
    }
    document.head.appendChild(script)
  })
  return scriptLoading
}

export function TelegramButton({
  config,
  onToken,
  onError,
  disabled = false,
  small = false,
  children,
}: {
  config: { client_id: number; nonce: string }
  onToken: (idToken: string) => void
  onError: (error: Error) => void
  disabled?: boolean
  small?: boolean
  children: string
}) {
  // Loaded ahead of the click: the popup has to open inside the click itself,
  // or the browser's popup blocker takes it for an unprompted window.
  useEffect(() => {
    loadScript().catch(() => {})
  }, [])

  function onClick() {
    const login = window.Telegram?.Login
    if (!login) {
      loadScript().catch(onError)
      onError(new Error('Вход через Telegram ещё загружается — нажмите через секунду'))
      return
    }
    login.auth(
      { client_id: config.client_id, nonce: config.nonce, lang: 'ru' },
      (result) => {
        if (result.id_token) onToken(result.id_token)
        // Closing the popup is the user changing their mind, not an error.
        else if (result.error && result.error !== 'popup_closed') {
          onError(new Error(`Telegram не подтвердил вход: ${result.error}`))
        }
      },
    )
  }

  return (
    <button
      type="button"
      className={small ? 'btn btn--quiet btn--small tg-login' : 'btn btn--quiet tg-login'}
      disabled={disabled}
      onClick={onClick}
    >
      <TelegramIcon />
      {children}
    </button>
  )
}

/** Profile row: link Telegram for login, or show what is linked. */
export function TelegramLink({ linkedAs }: { linkedAs: string | null }) {
  const config = useTelegramConfig()
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
      <div className="account__line">
        <span className="account__value" title="Вход через Telegram включён">
          {linkedAs}
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

  // Said rather than hidden: an empty section reads as a broken one.
  if (!config)
    return <p className="muted">Вход через Telegram на этом сервере не настроен.</p>
  return (
    <div className="account__line">
      <span className="account__value muted">Не привязан</span>
      <TelegramButton
        config={config}
        small
        disabled={isPending}
        onError={toast.error}
        onToken={(idToken) =>
          run(
            () =>
              api('/api/auth/telegram/link', {
                method: 'POST',
                json: { id_token: idToken },
              }),
            'Telegram привязан — теперь можно входить через него.',
          )
        }
      >
        Привязать
      </TelegramButton>
    </div>
  )
}
