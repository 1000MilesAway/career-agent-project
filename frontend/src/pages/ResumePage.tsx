/** "Моё резюме" — who the user is, and what the scan searches by.
 *
 * The parsed profile is shown readably rather than as collapsed JSON: whether
 * the model read the CV correctly is what decides every later score, and a
 * fold of raw JSON is no way to check it.
 */

import { useState, useTransition } from 'react'
import { useSWRConfig } from 'swr'
import {
  STATE_KEY,
  acceptSearchProfile,
  downloadProfile,
  proposeSearchProfile,
  uploadResume,
  useAppState,
} from '../api/hooks'
import type { ResumeProfile, SearchProfile } from '../api/types'
import { useToast } from '../components/toast'
import { Card, Chips, ErrorNote, Skeleton } from '../components/ui'
import { DownloadButtons } from '../components/DownloadButtons'
import { SearchProfileDiff } from '../components/SearchProfileDiff'

export function ResumePage() {
  const { data, isLoading, error } = useAppState()
  const { mutate } = useSWRConfig()
  const toast = useToast()

  const [file, setFile] = useState<File | null>(null)
  const [isUploading, startUpload] = useTransition()
  const [proposal, setProposal] = useState<{
    current: SearchProfile
    proposed: SearchProfile
  } | null>(null)
  const [isProposing, startProposing] = useTransition()

  function upload() {
    if (!file) return
    startUpload(async () => {
      try {
        await uploadResume(file)
        await mutate(STATE_KEY)
        setFile(null)
        toast.info('Резюме разобрано')
      } catch (e) {
        toast.error(e)
      }
    })
  }

  function propose() {
    startProposing(async () => {
      try {
        setProposal(await proposeSearchProfile())
      } catch (e) {
        toast.error(e)
      }
    })
  }

  async function accept(profile: SearchProfile) {
    try {
      await acceptSearchProfile(profile)
      await mutate(STATE_KEY)
      setProposal(null)
      toast.info('Профиль поиска обновлён')
    } catch (e) {
      toast.error(e)
    }
  }

  if (error) return <ErrorNote error={error} />
  if (isLoading || !data) return <Skeleton rows={5} />

  return (
    <>
      <Card title="Резюме">
        <p className="muted" style={{ marginTop: 0 }}>
          {data.has_resume
            ? `Загружено: ${data.resume_name}`
            : 'Резюме не загружено — без него подбор работать не будет.'}
        </p>
        <div className="row">
          <input
            type="file"
            accept=".pdf,.doc,.docx,.txt,.md"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <button className="btn" onClick={upload} disabled={!file || isUploading}>
            {isUploading ? 'Разбираю…' : 'Загрузить и разобрать'}
          </button>
        </div>
        <p className="muted" style={{ marginBottom: 0 }}>
          PDF, DOC, DOCX, TXT или MD до 10 МБ. Разбор стоит около 0.05 ₽ и делается один раз.
        </p>
      </Card>

      {data.profile ? <ProfileCard profile={data.profile} /> : null}

      {data.search_profile ? (
        <Card
          title="Профиль поиска"
          actions={
            <button
              className="btn btn--ghost btn--small"
              onClick={propose}
              disabled={isProposing}
            >
              {isProposing ? 'Думаю…' : 'Предложить заново'}
            </button>
          }
        >
          <p className="muted" style={{ marginTop: 0 }}>
            По этим спискам идёт бесплатный отсев. Фильтр, которого не видно, — это фильтр,
            который нельзя исправить.
          </p>
          <SearchProfileView profile={data.search_profile} />
        </Card>
      ) : null}

      {/* The agent proposes, the user accepts. Nothing is written without a
          click — the rule is a product entity, not a phrase in a prompt. */}
      {proposal ? (
        <SearchProfileDiff
          current={proposal.current}
          proposed={proposal.proposed}
          onAccept={accept}
          onReject={() => setProposal(null)}
        />
      ) : null}
    </>
  )
}

function ProfileCard({ profile }: { profile: ResumeProfile }) {
  return (
    <Card
      title="Профиль"
      actions={<DownloadButtons onDownload={downloadProfile} small label="Скачать резюме" />}
    >
      <dl className="kv">
        <dt>Имя</dt>
        <dd>{profile.name || '—'}</dd>
        <dt>Целевая роль</dt>
        <dd>{profile.target_role || '—'}</dd>
        {/* seniority is the level the experience shows; target_role is what the
            person calls themselves. The difference once cost a point on every
            senior posting — see docs/decisions.md. */}
        <dt>Уровень по опыту</dt>
        <dd>{profile.seniority || 'не определён'}</dd>
        <dt>Опыт</dt>
        <dd>{profile.years_experience ? `${profile.years_experience} лет` : '—'}</dd>
        <dt>Сфера</dt>
        <dd>{profile.field || '—'}</dd>
        {profile.directions?.length ? (
          <>
            <dt>Направления</dt>
            <dd>
              <Chips items={profile.directions} />
            </dd>
          </>
        ) : null}
        {profile.skills?.length ? (
          <>
            <dt>Навыки</dt>
            <dd>
              <Chips items={profile.skills} />
            </dd>
          </>
        ) : null}
      </dl>

      {profile.experience?.length ? (
        <>
          <h3 className="card__title" style={{ marginTop: 18 }}>
            Опыт
          </h3>
          {profile.experience.map((item, index) => (
            <div key={`${item.company}-${index}`} style={{ marginBottom: 12 }}>
              <div>
                <b>{item.role}</b> — {item.company}
              </div>
              <div className="muted">{item.period}</div>
              <ul style={{ margin: '4px 0' }}>
                {item.achievements?.map((achievement, i) => (
                  <li key={i}>{achievement}</li>
                ))}
              </ul>
            </div>
          ))}
        </>
      ) : null}

      <details style={{ marginTop: 10 }}>
        <summary className="muted">Исходный JSON</summary>
        <pre>{JSON.stringify(profile, null, 2)}</pre>
      </details>
    </Card>
  )
}

function SearchProfileView({ profile }: { profile: SearchProfile }) {
  return (
    <dl className="kv">
      <dt>Запросы</dt>
      <dd>
        <Chips items={profile.queries} />
      </dd>
      <dt>Ключевые слова</dt>
      <dd>
        <Chips items={profile.positive} />
      </dd>
      <dt>Стоп-слова</dt>
      <dd>
        <Chips items={profile.negative} negative />
      </dd>
      <dt>Не та специализация</dt>
      <dd>
        <Chips items={profile.divisions_negative} negative />
      </dd>
      <dt>Минимальный грейд</dt>
      <dd>{profile.min_qualification || 'любой'}</dd>
    </dl>
  )
}
