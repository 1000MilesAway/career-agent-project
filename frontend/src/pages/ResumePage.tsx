/** "Моё резюме" — who the user is, and what the scan searches by.
 *
 * The parsed profile is shown readably rather than as collapsed JSON: whether
 * the model read the CV correctly is what decides every later score, and a
 * fold of raw JSON is no way to check it.
 */

import { useState, useTransition } from 'react'
import { useSWRConfig } from 'swr'
import {
  CHAT_KEY,
  STATE_KEY,
  acceptSearchProfile,
  profileExportBase,
  proposeSearchProfile,
  restoreResume,
  uploadResume,
  useAppState,
} from '../api/hooks'
import type { ResumeProfile, ResumeVersion, SearchProfile } from '../api/types'
import { useToast } from '../components/toast'
import { Card, Chips, ErrorNote, PageHead, Skeleton } from '../components/ui'
import { DownloadButtons } from '../components/DownloadButtons'
import { ProfileEditor } from '../components/ProfileEditor'
import { setTourStep } from '../components/tour'
import { SearchProfileDiff } from '../components/SearchProfileDiff'
import './resume.css'

export function ResumePage() {
  const { data, isLoading, error } = useAppState()
  const { mutate } = useSWRConfig()
  const toast = useToast()

  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [isUploading, startUpload] = useTransition()
  const [proposal, setProposal] = useState<{
    current: SearchProfile
    proposed: SearchProfile
  } | null>(null)
  const [isProposing, startProposing] = useTransition()
  const [editing, setEditing] = useState(false)

  function upload() {
    if (!file) return
    startUpload(async () => {
      // Read before the upload: afterwards the state says there is a CV.
      const first = !data?.has_resume
      try {
        await uploadResume(file)
        await mutate(STATE_KEY)
        setFile(null)
        toast.info('Резюме разобрано')
        // The user stays to check how the CV was read; a hint on the sidebar
        // points to the search as the next step.
        if (first) setTourStep('search')
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

  if (error)
    return (
      <div className="page">
        <ErrorNote error={error} />
      </div>
    )
  if (isLoading || !data)
    return (
      <div className="page">
        <Skeleton rows={5} />
      </div>
    )

  const uploader = (
    <div className="upload">
      <label
        className={dragging ? 'drop drop--over' : 'drop'}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          setFile(e.dataTransfer.files?.[0] ?? null)
        }}
      >
        <input
          type="file"
          className="sr-only"
          accept=".pdf,.doc,.docx,.rtf,.txt,.md"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        {file ? (
          <span className="drop__file">{file.name}</span>
        ) : (
          <>
            <span className="drop__title">
              {data.has_resume ? 'Заменить файл' : 'Перетащите файл сюда'}
            </span>
            <span className="drop__hint">или нажмите, чтобы выбрать</span>
          </>
        )}
      </label>
      {file ? (
        <div className="row">
          <button className="btn" onClick={upload} disabled={isUploading}>
            {isUploading ? 'Разбираю…' : 'Загрузить и разобрать'}
          </button>
          <button
            className="btn btn--quiet"
            onClick={() => setFile(null)}
            disabled={isUploading}
          >
            Отмена
          </button>
        </div>
      ) : null}
      <p className="muted upload__note">PDF, DOC, DOCX, RTF, TXT или MD до 10 МБ.</p>
    </div>
  )

  if (!data.has_resume || !data.profile) {
    return (
      <div className="page">
        <PageHead
          title="Моё резюме"
          lede="С него всё начинается: по резюме строятся запросы, фильтры и оценка каждой вакансии."
        />
        <div className="card resume-first">{uploader}</div>
      </div>
    )
  }

  return (
    <div className="page">
      <PageHead
        title="Моё резюме"
        lede="Так модель прочитала ваше резюме. Каждая оценка строится на этом — если что-то не так, поправьте вручную или через помощника."
        actions={
          <DownloadButtons exportBase={profileExportBase} small label="Скачать резюме" />
        }
      />

      <div className="resume">
        <div className="resume__main">
          {editing ? (
            <ProfileEditor profile={data.profile} onClose={() => setEditing(false)} />
          ) : (
            <ProfileCard profile={data.profile} onEdit={() => setEditing(true)} />
          )}
        </div>

        <aside className="resume__side">
          <section className="card">
            <h2 className="card__title">Файл</h2>
            <p className="resume__file">{data.resume_name}</p>
            {uploader}
          </section>

          {data.resume_history && data.resume_history.length > 1 ? (
            <VersionHistory versions={data.resume_history} />
          ) : null}
        </aside>
      </div>

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
          <p className="muted" style={{ marginTop: 0, marginBottom: 18 }}>
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
    </div>
  )
}

function ProfileCard({ profile, onEdit }: { profile: ResumeProfile; onEdit: () => void }) {
  return (
    <section className="card profile">
      <header className="profile__head">
        <div>
          <h2 className="profile__name">{profile.name || 'Имя не найдено'}</h2>
          <p className="profile__role">
            {profile.target_role || 'Целевая роль не найдена'}
          </p>
        </div>
        <button
          className="btn btn--ghost btn--small"
          onClick={onEdit}
          title="Сохранится новой версией — прежнюю можно вернуть"
        >
          Редактировать
        </button>
      </header>

      <dl className="facts">
        {/* seniority is the level the experience shows; target_role is what the
            person calls themselves. The difference once cost a point on every
            senior posting — see docs/decisions.md. */}
        <div>
          <dt>Уровень по опыту</dt>
          <dd>{profile.seniority || 'не определён'}</dd>
        </div>
        <div>
          <dt>Опыт</dt>
          <dd>{profile.years_experience ? `${profile.years_experience} лет` : '—'}</dd>
        </div>
        <div>
          <dt>Сфера</dt>
          <dd>{profile.field || '—'}</dd>
        </div>
      </dl>

      {/* What goes into the downloaded file must be visible here first:
          summary, description and education reach the employer. */}
      {profile.summary ? (
        <div className="profile__group">
          <h3 className="eyebrow">О себе</h3>
          <p className="profile__summary">{profile.summary}</p>
        </div>
      ) : null}

      {profile.directions?.length ? (
        <div className="profile__group">
          <h3 className="eyebrow">Направления</h3>
          <Chips items={profile.directions} />
        </div>
      ) : null}

      {profile.skills?.length ? (
        <div className="profile__group">
          <h3 className="eyebrow">Навыки · {profile.skills.length}</h3>
          <Chips items={profile.skills} />
        </div>
      ) : null}

      {profile.experience?.length ? (
        <div className="profile__group">
          <h3 className="eyebrow">Опыт</h3>
          <ol className="timeline">
            {profile.experience.map((item, index) => (
              <li key={`${item.company}-${index}`} className="timeline__item">
                <p className="timeline__role">
                  {item.role ? (
                    <>
                      {item.role}{' '}
                      <span className="timeline__company">· {item.company}</span>
                    </>
                  ) : (
                    item.company
                  )}
                </p>
                <p className="timeline__period">
                  {[item.period, item.location].filter(Boolean).join(' · ')}
                </p>
                {item.description ? (
                  <p className="timeline__description">{item.description}</p>
                ) : null}
                {item.achievements?.length ? (
                  <ul className="timeline__list">
                    {item.achievements.map((achievement, i) => (
                      <li key={i}>{achievement}</li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {profile.education?.length ? (
        <div className="profile__group">
          <h3 className="eyebrow">Образование</h3>
          <ul className="profile__list">
            {profile.education.map((entry, i) => (
              <li key={i}>{entry}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <details className="profile__raw">
        <summary className="muted">Исходный JSON</summary>
        <pre>{JSON.stringify(profile, null, 2)}</pre>
      </details>
    </section>
  )
}

/** Under a fold on purpose: the user asked not to dig through versions on the
 * main screen — see docs/decisions.md, 2026-09-22. */
function VersionHistory({ versions }: { versions: ResumeVersion[] }) {
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const [restoring, startRestoring] = useTransition()

  function restore(version: ResumeVersion) {
    startRestoring(async () => {
      try {
        await restoreResume(version.id)
        await Promise.all([mutate(STATE_KEY), mutate(CHAT_KEY)])
        toast.info(`Вернули версию ${version.revision}`)
      } catch (e) {
        toast.error(e)
      }
    })
  }

  return (
    <Card title="История версий">
      <details>
        <summary className="muted">
          Версий: {versions.length}. Прежние не затираются — любую можно вернуть.
        </summary>
        <ol className="versions">
          {versions.map((version) => (
            <li key={version.id} className="versions__item">
              <div className="row">
                <b>Версия {version.revision}</b>
                <span className="muted">
                  {new Date(version.created_at).toLocaleString('ru-RU', {
                    dateStyle: 'short',
                    timeStyle: 'short',
                  })}{' '}
                  · {version.filename}
                </span>
                {version.active ? (
                  <span className="chip">текущая</span>
                ) : (
                  <button
                    className="btn btn--ghost btn--small"
                    onClick={() => restore(version)}
                    disabled={restoring}
                    title="Бесплатно. Создаст новую версию с этим содержимым — вернуться обратно тоже можно"
                  >
                    Вернуть
                  </button>
                )}
              </div>
              <ul className="muted" style={{ margin: '4px 0' }}>
                {version.changes.map((change, i) => (
                  <li key={i}>{change}</li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      </details>
    </Card>
  )
}

function SearchProfileView({ profile }: { profile: SearchProfile }) {
  return (
    <div className="sprofile">
      <div className="sprofile__col">
        <p className="eyebrow">Ищем по запросам</p>
        <Chips items={profile.queries} />
        <p className="eyebrow">Берём, если есть</p>
        <Chips items={profile.positive} />
        <p className="eyebrow">Минимальный грейд</p>
        <p className="sprofile__grade">{profile.min_qualification || 'любой'}</p>
      </div>
      <div className="sprofile__col">
        <p className="eyebrow">Отсеиваем по стоп-словам</p>
        <Chips items={profile.negative} negative />
        <p className="eyebrow">Не та специализация</p>
        <Chips items={profile.divisions_negative} negative />
      </div>
    </div>
  )
}
