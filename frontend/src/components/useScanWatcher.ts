/** Tell the user a scan finished, whichever page they are on.
 *
 * Mounted in the cabinet shell rather than on the search page: the point of
 * running the scan as a job is that the user may leave the page while it
 * runs, and whatever reports the end has to still be there when it comes.
 */

import { useEffect, useRef } from 'react'
import { useSWRConfig } from 'swr'
import { STATE_KEY, isActive, useScanJob } from '../api/hooks'
import type { ScanJob } from '../api/types'
import { useToast } from './toast'

export function useScanWatcher(): ScanJob | null {
  const { job } = useScanJob()
  const { mutate } = useSWRConfig()
  const toast = useToast()
  // undefined = nothing seen yet. A scan that finished before the tab was
  // opened is not news, so only a change observed here is reported.
  const seen = useRef<ScanJob | null | undefined>(undefined)

  useEffect(() => {
    const before = seen.current
    seen.current = job
    if (!before || !job || before.id !== job.id) return
    if (!isActive(before) || isActive(job)) return

    // The rows were written in one commit at the end, so the list is
    // refreshed once, now, rather than polled alongside the job.
    void mutate(STATE_KEY)
    if (job.status === 'failed') {
      toast.error(`Поиск не удался: ${job.error ?? 'причина не записана'}`)
    } else {
      toast.info(`Поиск закончен: новых вакансий ${job.result?.new ?? 0}.`)
    }
  }, [job, mutate, toast])

  return job
}
