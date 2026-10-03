/** Tell the user «умный отбор» finished, whichever page they are on.
 *
 * The triage is a job on the server, like a scan: awaited inside the page it
 * held the cabinet's navigation for as long as the model thought. Mounted in
 * the cabinet shell for the same reason the scan watcher is — the user may
 * leave the page while it runs.
 */

import { useEffect, useRef } from 'react'
import { useSWRConfig } from 'swr'
import { STATE_KEY, isActive, markTriageSettled, useTriageJob } from '../api/hooks'
import type { TriageJob } from '../api/types'
import { useToast } from './toast'

export function useTriageWatcher(): TriageJob | null {
  const { job } = useTriageJob()
  const { mutate } = useSWRConfig()
  const toast = useToast()
  // undefined = nothing seen yet. A triage that finished before the tab was
  // opened is not news, so only a change observed here is reported.
  const seen = useRef<TriageJob | null | undefined>(undefined)

  useEffect(() => {
    const before = seen.current
    seen.current = job
    if (!before || !job || before.id !== job.id) return
    if (!isActive(before) || isActive(job)) return

    if (job.status === 'failed') {
      toast.error(`Умный отбор не удался: ${job.error ?? 'причина не записана'}`)
      return
    }
    // The verdicts reach the list first, then the search page is told to
    // take its order anew — on the rows that carry them.
    void mutate(STATE_KEY).then(() => markTriageSettled(job.finished_at))

    const result = job.result
    if (!result) return
    if (result.note) {
      toast.info(result.note)
      return
    }
    toast.info(`Умный отбор: разобрано ${result.judged}.`)
    // A short reply leaves postings unjudged. They are reported, never
    // backfilled as "skip": an unjudged vacancy is not a rejected one.
    if (result.missing?.length) {
      toast.error(`Без вердикта осталось ${result.missing.length}.`)
    }
    // Ids the model invented. Saying so makes a broken prompt look broken.
    if (result.unknown?.length) {
      toast.error(
        `Модель вернула ${result.unknown.length} чужих id — вердикты по ним отброшены.`,
      )
    }
  }, [job, mutate, toast])

  return job
}
