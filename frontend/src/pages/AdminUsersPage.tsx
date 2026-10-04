/** Пользователи: every account, what it has done and what it has cost.
 *
 * Search, sort and page live in the URL rather than in component state: a
 * reload or a link to a colleague lands on the same slice of the list. The
 * search box itself is fast state; the request follows it through
 * useDeferredValue, so typing never waits for the network.
 */

import { useDeferredValue } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAdminUsers } from '../api/hooks'
import type { AdminUser, AdminUserSort } from '../api/types'
import { Empty, ErrorNote, PageHead, Skeleton } from '../components/ui'
import { formatCount, formatDate, formatDateTime, formatRub } from './adminFormat'
import './admin.css'

const PAGE_SIZE = 50

const SORTS: { value: AdminUserSort; label: string }[] = [
  { value: 'created', label: 'Новые' },
  { value: 'spent', label: 'Больше потратили' },
  { value: 'last_call', label: 'Недавно активные' },
]

const isSort = (value: string | null): value is AdminUserSort =>
  SORTS.some((s) => s.value === value)

export function AdminUsersPage() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const sortParam = params.get('sort')
  const sort: AdminUserSort = isSort(sortParam) ? sortParam : 'created'
  const page = Math.max(0, Number(params.get('page')) || 0)

  const deferredQ = useDeferredValue(q)
  const { data, error, isLoading, isValidating } = useAdminUsers({
    q: deferredQ.trim(),
    sort,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  })

  // Any change but paging starts from the first page: page 3 of an old
  // search is not a page of the new one.
  function update(changes: Record<string, string | null>) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current)
        for (const [key, value] of Object.entries(changes)) {
          if (value) next.set(key, value)
          else next.delete(key)
        }
        if (!('page' in changes)) next.delete('page')
        return next
      },
      { replace: true },
    )
  }

  const total = data?.total ?? 0
  const first = page * PAGE_SIZE
  const stale = isValidating || deferredQ !== q

  return (
    <div className="page page--wide">
      <PageHead
        title="Пользователи"
        lede={
          data ? `Всего учёток: ${formatCount(total)}. Деньги видны только здесь.` : null
        }
      />

      <div className="admin-toolbar">
        <input
          type="search"
          className="input admin-toolbar__search"
          placeholder="Почта, имя или Telegram"
          aria-label="Поиск по пользователям"
          value={q}
          onChange={(event) => update({ q: event.target.value || null })}
        />
        <div className="seg" role="group" aria-label="Сортировка">
          {SORTS.map((option) => (
            <button
              key={option.value}
              type="button"
              className="seg__item"
              aria-pressed={sort === option.value}
              onClick={() =>
                update({ sort: option.value === 'created' ? null : option.value })
              }
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <ErrorNote error={error} />

      {isLoading && !data ? (
        <Skeleton rows={6} />
      ) : data && data.items.length === 0 ? (
        <Empty title={q ? 'Никого не нашлось' : 'Учёток пока нет'}>
          {q ? <p className="muted">Поиск идёт по почте, имени и Telegram.</p> : null}
        </Empty>
      ) : data ? (
        <>
          <div className="table-wrap" aria-busy={stale}>
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Пользователь</th>
                  <th scope="col">Тариф</th>
                  <th
                    scope="col"
                    className="table__num"
                    title="Израсходовано лимита: день / неделя"
                  >
                    Лимит
                  </th>
                  <th scope="col" className="table__num">
                    Вакансий
                  </th>
                  <th scope="col" className="table__num">
                    Оценок
                  </th>
                  <th scope="col" className="table__num">
                    За 30 дней
                  </th>
                  <th scope="col" className="table__num">
                    Всего
                  </th>
                  <th scope="col">Последний вызов</th>
                  <th scope="col">Создана</th>
                </tr>
              </thead>
              <tbody className={stale ? 'table__body--stale' : undefined}>
                {data.items.map((user) => (
                  <UserRow key={user.id} user={user} />
                ))}
              </tbody>
            </table>
          </div>

          {total > PAGE_SIZE ? (
            <nav className="pager" aria-label="Страницы">
              <span className="muted num">
                {formatCount(first + 1)}–{formatCount(Math.min(first + PAGE_SIZE, total))}{' '}
                из {formatCount(total)}
              </span>
              <button
                type="button"
                className="btn btn--ghost btn--small"
                disabled={page === 0}
                onClick={() => update({ page: page > 1 ? String(page - 1) : null })}
              >
                Назад
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--small"
                disabled={first + PAGE_SIZE >= total}
                onClick={() => update({ page: String(page + 1) })}
              >
                Вперёд
              </button>
            </nav>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

const PLAN_TITLES: Record<AdminUser['plan'], string> = {
  free: 'Бесплатный',
  paid: 'Подписка',
  admin: 'Админ',
}

function UserRow({ user }: { user: AdminUser }) {
  // The login is the identity; a name is shown over it when there is one.
  const login = user.email ?? user.telegram_name ?? '—'
  return (
    <tr>
      <td>
        <div className="who">
          <span className="who__main">{user.name || login}</span>
          {user.name ? <span className="who__sub">{login}</span> : null}
          {user.email && user.telegram_name ? (
            <span className="who__sub">{user.telegram_name}</span>
          ) : null}
          <span className="who__tags">
            {!user.is_active ? <span className="badge badge--bad">отключена</span> : null}
            {user.email && !user.email_verified ? (
              <span className="badge badge--warn">почта не подтверждена</span>
            ) : null}
            {!user.has_resume ? <span className="badge">без резюме</span> : null}
          </span>
        </div>
      </td>
      <td>
        <span className={`badge badge--plan-${user.plan}`}>{PLAN_TITLES[user.plan]}</span>
        {user.paid_until ? (
          <div className="who__sub">до {formatDate(user.paid_until)}</div>
        ) : null}
      </td>
      <td className="table__num">
        {user.day_pct == null && user.week_pct == null ? (
          <span className="muted">—</span>
        ) : (
          <span title="день / неделя">
            <Pct value={user.day_pct} /> / <Pct value={user.week_pct} />
          </span>
        )}
      </td>
      <td className="table__num">{formatCount(user.vacancies)}</td>
      <td className="table__num">{formatCount(user.evaluations)}</td>
      <td className="table__num">{formatRub(user.spent_30d_rub)}</td>
      <td className="table__num">{formatRub(user.spent_rub)}</td>
      <td className="table__date">
        {user.last_call_at ? (
          formatDateTime(user.last_call_at)
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td className="table__date">{formatDate(user.created_at)}</td>
    </tr>
  )
}

/** A window at its ceiling is flagged in words as well as colour. */
function Pct({ value }: { value: number | null }) {
  if (value == null) return <span className="muted">—</span>
  return value >= 100 ? (
    <b className="pct--full" title="лимит исчерпан">
      {value}%
    </b>
  ) : (
    <>{value}%</>
  )
}
