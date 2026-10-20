/** Buying and showing a report or one of the three documents.
 *
 * Shared by search and the shortlist so the two cannot drift apart on what a
 * click costs. Everything paid for is fetched through here, and a cached
 * response never triggers a revalidation: reopening a stored document costs
 * nothing and must not look like it did.
 */

import { useCallback, useState } from 'react'
import { useSWRConfig } from 'swr'
import { STATE_KEY, buyArtifact, evaluateVacancy } from '../api/hooks'
import type {
  ApplyStepName,
  ArtifactContent,
  CoverLetter,
  FoundVacancy,
  InterviewPrep,
  Report,
  TailoredResume,
} from '../api/types'
import { Drawer } from './drawer'
import {
  CoverLetterView,
  InterviewPrepView,
  type LetterDelivery,
  ReportView,
  TailoredResumeView,
} from './report'
import { useToast } from './toast'
import { Skeleton } from './ui'

const STEP_TITLES: Record<ApplyStepName, string> = {
  tailor: 'Резюме под вакансию',
  letter: 'Сопроводительное письмо',
  interview: 'Подготовка к интервью',
}

type PanelContent =
  { type: 'report'; report: Report } | { type: ApplyStepName; content: ArtifactContent }

interface PanelState {
  title: string
  /** null while the paid call is in flight. */
  content: PanelContent | null
  /** A vacancy from a Telegram post is answered in a chat more often than on
   *  a board, so its letter opens on the message. */
  prefer?: LetterDelivery
}

const preferFor = (vacancy: FoundVacancy): LetterDelivery =>
  vacancy.source === 'telegram' || vacancy.source === 'telethon' ? 'message' : 'letter'

export function useDocumentPanel() {
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const [panel, setPanel] = useState<PanelState | null>(null)
  /** Ids with a paid call in flight, so only that row's button is busy. */
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set())

  const mark = useCallback((key: string, on: boolean) => {
    setBusy((current) => {
      const next = new Set(current)
      if (on) next.add(key)
      else next.delete(key)
      return next
    })
  }, [])

  const openReport = useCallback(
    async (vacancy: FoundVacancy, fresh = false) => {
      // Already on the row: show it without asking the server again. `fresh`
      // is the re-evaluation of an outdated score, which is a purchase.
      if (vacancy.report && !fresh) {
        setPanel({
          title: vacancy.title,
          content: { type: 'report', report: vacancy.report },
        })
        return
      }

      const key = `${vacancy.id}:report`
      mark(key, true)
      setPanel({ title: vacancy.title, content: null })
      try {
        const data = await evaluateVacancy(vacancy.id)
        setPanel({
          title: vacancy.title,
          content: { type: 'report', report: data.report },
        })
        // A fresh score changed what is stored; a cached one changed nothing.
        if (!data.cached) await mutate(STATE_KEY)
      } catch (e) {
        setPanel(null)
        toast.error(e)
      } finally {
        mark(key, false)
      }
    },
    [mark, mutate, toast],
  )

  const openArtifact = useCallback(
    async (vacancy: FoundVacancy, step: ApplyStepName) => {
      const stored = vacancy.artifacts?.[step]
      if (stored) {
        setPanel({
          title: `${STEP_TITLES[step]} — ${vacancy.title}`,
          content: { type: step, content: stored },
          prefer: preferFor(vacancy),
        })
        return
      }

      const key = `${vacancy.id}:${step}`
      mark(key, true)
      setPanel({
        title: `${STEP_TITLES[step]} — ${vacancy.title}`,
        content: null,
      })
      try {
        const data = await buyArtifact(vacancy.id, step)
        setPanel({
          title: `${STEP_TITLES[step]} — ${vacancy.title}`,
          content: { type: step, content: data.content },
          prefer: preferFor(vacancy),
        })
        if (!data.cached) await mutate(STATE_KEY)
      } catch (e) {
        setPanel(null)
        toast.error(e)
      } finally {
        mark(key, false)
      }
    },
    [mark, mutate, toast],
  )

  const isBusy = useCallback((key: string) => busy.has(key), [busy])

  const element = (
    <Drawer open={panel !== null} title={panel?.title ?? ''} onClose={() => setPanel(null)}>
      {panel ? <PanelBody panel={panel} /> : null}
    </Drawer>
  )

  return { openReport, openArtifact, isBusy, panel: element }
}

function PanelBody({ panel }: { panel: PanelState }) {
  if (!panel.content) {
    return (
      <>
        <p className="muted">Считаю. Это занимает 13–15 секунд.</p>
        <Skeleton rows={6} />
      </>
    )
  }

  const { content } = panel
  return (
    <>
      {content.type === 'report' ? <ReportView report={content.report} /> : null}
      {content.type === 'letter' ? (
        <CoverLetterView value={content.content as CoverLetter} prefer={panel.prefer} />
      ) : null}
      {content.type === 'tailor' ? (
        <TailoredResumeView value={content.content as TailoredResume} />
      ) : null}
      {content.type === 'interview' ? (
        <InterviewPrepView value={content.content as InterviewPrep} />
      ) : null}
    </>
  )
}
