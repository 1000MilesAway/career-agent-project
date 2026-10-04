/** The confirmation layer, on screen.
 *
 * The product rule is that the agent proposes and the user accepts: nothing
 * generated is written to state without an explicit yes. The old page had no
 * screen for this at all — the endpoint existed and was unreachable, so the
 * only search profile a user could ever have was the one derived silently on
 * upload.
 *
 * Silence is refusal: closing this without accepting changes nothing. Fields
 * the proposal does not touch are not overwritten, which is why the accepted
 * object is built from `current` and not from `proposed` alone.
 */

import { useMemo, useState } from 'react'
import type { SearchProfile } from '../api/types'
import { Card } from './ui'

/** The list fields a proposal may change, with their labels. */
const LIST_FIELDS: { key: keyof SearchProfile; title: string }[] = [
  { key: 'queries', title: 'Запросы' },
  { key: 'positive', title: 'Ключевые слова' },
  { key: 'negative', title: 'Стоп-слова' },
  { key: 'divisions_negative', title: 'Не та специализация' },
]

interface ListDiff {
  key: keyof SearchProfile
  title: string
  kept: string[]
  added: string[]
  removed: string[]
}

function diffList(
  current: string[] = [],
  proposed: string[] = [],
): Omit<ListDiff, 'key' | 'title'> {
  // Sets, not indexOf in a loop: these lists reach a few dozen entries and
  // the comparison runs on every render of the panel.
  const currentSet = new Set(current)
  const proposedSet = new Set(proposed)
  return {
    kept: proposed.filter((item) => currentSet.has(item)),
    added: proposed.filter((item) => !currentSet.has(item)),
    removed: current.filter((item) => !proposedSet.has(item)),
  }
}

export function SearchProfileDiff({
  current,
  proposed,
  onAccept,
  onReject,
}: {
  current: SearchProfile
  proposed: SearchProfile
  onAccept: (profile: SearchProfile) => void | Promise<void>
  onReject: () => void
}) {
  const [busy, setBusy] = useState(false)

  const diffs = useMemo<ListDiff[]>(
    () =>
      LIST_FIELDS.map(({ key, title }) => ({
        key,
        title,
        ...diffList(
          current[key] as string[] | undefined,
          proposed[key] as string[] | undefined,
        ),
      })),
    [current, proposed],
  )

  const gradeChanged =
    (current.min_qualification ?? null) !== (proposed.min_qualification ?? null)
  const changed = gradeChanged || diffs.some((d) => d.added.length || d.removed.length)

  async function accept() {
    setBusy(true)
    try {
      // Merged onto current: a field the proposal is silent about keeps its
      // value rather than being wiped by an absent key.
      await onAccept({ ...current, ...proposed })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Предложение модели">
      {changed ? (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>поле</th>
                  <th>добавится</th>
                  <th>уберётся</th>
                  <th>останется</th>
                </tr>
              </thead>
              <tbody>
                {diffs.map((diff) => (
                  <tr key={String(diff.key)}>
                    <td>{diff.title}</td>
                    <td>
                      {diff.added.length ? (
                        <div className="chips">
                          {diff.added.map((item) => (
                            <span key={item} className="chip chip--add">
                              + {item}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      {diff.removed.length ? (
                        <div className="chips">
                          {diff.removed.map((item) => (
                            <span key={item} className="chip chip--neg">
                              − {item}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="muted">{diff.kept.length}</td>
                  </tr>
                ))}
                {gradeChanged ? (
                  <tr>
                    <td>Минимальный грейд</td>
                    <td className="m-full">{proposed.min_qualification || 'любой'}</td>
                    <td className="m-none">{current.min_qualification || 'любой'}</td>
                    <td className="muted">—</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="muted" style={{ marginTop: 0 }}>
          Модель предложила то же самое — менять нечего.
        </p>
      )}

      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn" onClick={accept} disabled={busy || !changed}>
          {busy ? 'Сохраняю…' : 'Принять'}
        </button>
        <button className="btn btn--ghost" onClick={onReject} disabled={busy}>
          Отказаться
        </button>
      </div>
    </Card>
  )
}
