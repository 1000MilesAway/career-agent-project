/** Tell the user a scan finished, whichever page they are on.
 *
 * Mounted in the cabinet shell rather than on the search page: the point of
 * running the scan as a job is that the user may leave the page while it
 * runs, and whatever reports the end has to still be there when it comes.
 *
 * A scan answers from the shared pool; listings past their freshness are
 * re-read by a "refresh" job the worker queues behind it. Its end is watched
 * here too, so the vacancies it finds reach the list without another press.
 */

import { useEffect, useRef } from 'react'
import { useSWRConfig } from 'swr'
import {
  REFRESH_JOB_KEY,
  STATE_KEY,
  isActive,
  useRefreshJob,
  useScanJob,
} from '../api/hooks'
import type { ScanJob } from '../api/types'
import { useToast } from './toast'

/** Call `onEnd` once when a job seen running here stops. */
function useJobEnd(job: ScanJob | null, onEnd: (job: ScanJob) => void): void {
  // undefined = nothing seen yet. A job that finished before the tab was
  // opened is not news, so only a change observed here is reported.
  const seen = useRef<ScanJob | null | undefined>(undefined)

  useEffect(() => {
    const before = seen.current
    seen.current = job
    if (!before || !job || before.id !== job.id) return
    if (!isActive(before) || isActive(job)) return
    onEnd(job)
  }, [job, onEnd])
}

export function useScanWatcher(): ScanJob | null {
  const { job } = useScanJob()
  const { job: refresh } = useRefreshJob()
  const { mutate } = useSWRConfig()
  const toast = useToast()

  useJobEnd(job, (done) => {
    // The rows were written in one commit at the end, so the list is
    // refreshed once, now, rather than polled alongside the job.
    void mutate(STATE_KEY)
    if (done.status === 'failed') {
      toast.error(`Поиск не удался: ${done.error ?? 'причина не записана'}`)
      return
    }
    // No count here: the server's also covers rows the list folds, and a
    // second number beside the page's own did not add up. The page counts
    // what it shows and marks those rows.
    const found = done.result?.new
      ? 'Поиск закончен, новые вакансии отмечены в списке.'
      : 'Поиск закончен: новых вакансий нет.'
    if (done.result?.deferred) {
      // The refresh is already queued; asking for it starts its polling.
      void mutate(REFRESH_JOB_KEY)
      toast.info(`${found} Источники обновляются в фоне — свежие вакансии появятся сами.`)
    } else {
      toast.info(found)
    }
  })

  useJobEnd(refresh, (done) => {
    void mutate(STATE_KEY)
    if (done.status === 'failed') {
      toast.error(`Источники не обновились: ${done.error ?? 'причина не записана'}`)
    } else if (done.result?.new) {
      toast.info('Источники обновлены, новые вакансии добавлены в список.')
    }
  })

  return job
}
