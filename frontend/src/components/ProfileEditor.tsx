/** The parsed CV, corrected by hand.
 *
 * Lists are edited as one item per line: a chip editor per list would be five
 * widgets to learn, and a textarea is what a CV is written in anyway.
 */

import { useState, useTransition, type FormEvent } from 'react'
import { useSWRConfig } from 'swr'
import { CHAT_KEY, STATE_KEY, editResumeProfile } from '../api/hooks'
import type { ResumeProfile } from '../api/types'
import {
  SENIORITY,
  fromDraft,
  jobDraft,
  toDraft,
  type JobDraft,
  type ProfileDraft,
} from './profileDraft'
import { useToast } from './toast'

export function ProfileEditor({
  profile,
  onClose,
}: {
  profile: ResumeProfile
  onClose: () => void
}) {
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const [initial] = useState(() => toDraft(profile))
  const [draft, setDraft] = useState(initial)
  const [isSaving, startSaving] = useTransition()

  const set = <K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }))

  const setJob = (index: number, patch: Partial<JobDraft>) =>
    setDraft((d) => ({
      ...d,
      experience: d.experience.map((job, i) => (i === index ? { ...job, ...patch } : job)),
    }))

  function moveJob(index: number, by: -1 | 1) {
    setDraft((d) => {
      const jobs = [...d.experience]
      const [job] = jobs.splice(index, 1)
      jobs.splice(index + by, 0, job)
      return { ...d, experience: jobs }
    })
  }

  const dirty = JSON.stringify(fromDraft(draft)) !== JSON.stringify(fromDraft(initial))

  function cancel() {
    if (dirty && !window.confirm('Выйти без сохранения? Правки пропадут.')) return
    onClose()
  }

  function save(event: FormEvent) {
    event.preventDefault()
    if (!dirty) return onClose()
    startSaving(async () => {
      try {
        const saved = await editResumeProfile(fromDraft(draft))
        await Promise.all([mutate(STATE_KEY), mutate(CHAT_KEY)])
        toast.info(`Сохранено как версия ${saved.revision}`)
        onClose()
      } catch (e) {
        toast.error(e)
      }
    })
  }

  return (
    <form className="card profile editor" onSubmit={save}>
      <div className="editor__grid">
        <Text label="Имя" value={draft.name} onChange={(v) => set('name', v)} />
        <Text
          label="Целевая должность"
          value={draft.target_role}
          onChange={(v) => set('target_role', v)}
        />
        <label className="editor__field">
          <span className="eyebrow">Уровень по опыту</span>
          <select
            className="input"
            value={draft.seniority}
            onChange={(e) => set('seniority', e.target.value)}
          >
            <option value="">не определён</option>
            {SENIORITY.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </label>
        <Text
          label="Лет опыта"
          value={draft.years_experience}
          inputMode="decimal"
          onChange={(v) => set('years_experience', v)}
        />
        <Text label="Сфера" value={draft.field} onChange={(v) => set('field', v)} />
      </div>

      <Area
        label="О себе"
        hint="уходит работодателю в файле резюме"
        value={draft.summary}
        rows={4}
        onChange={(v) => set('summary', v)}
      />
      <Area
        label="Направления"
        hint="по одному в строке"
        value={draft.directions}
        onChange={(v) => set('directions', v)}
      />
      <Area
        label="Навыки"
        hint="по одному в строке"
        value={draft.skills}
        rows={6}
        onChange={(v) => set('skills', v)}
      />

      <fieldset className="editor__jobs">
        <legend className="eyebrow">Опыт</legend>
        {draft.experience.map((job, index) => (
          <div key={job.key} className="editor__job">
            <div className="editor__grid">
              <Text
                label="Должность"
                value={job.role}
                onChange={(v) => setJob(index, { role: v })}
              />
              <Text
                label="Компания"
                value={job.company}
                onChange={(v) => setJob(index, { company: v })}
              />
              <Text
                label="Период"
                value={job.period}
                placeholder="2021 — наст. время"
                onChange={(v) => setJob(index, { period: v })}
              />
              <Text
                label="Город"
                value={job.location}
                placeholder="или «Удалённо»"
                onChange={(v) => setJob(index, { location: v })}
              />
            </div>
            <Text
              label="Чем занималась компания"
              value={job.description}
              onChange={(v) => setJob(index, { description: v })}
            />
            <Area
              label="Достижения"
              hint="по одному в строке, с цифрами, если есть"
              value={job.achievements}
              rows={4}
              onChange={(v) => setJob(index, { achievements: v })}
            />
            <div className="row">
              <button
                type="button"
                className="btn btn--quiet btn--small"
                disabled={index === 0}
                onClick={() => moveJob(index, -1)}
              >
                Выше
              </button>
              <button
                type="button"
                className="btn btn--quiet btn--small"
                disabled={index === draft.experience.length - 1}
                onClick={() => moveJob(index, 1)}
              >
                Ниже
              </button>
              <button
                type="button"
                className="btn btn--quiet btn--small"
                onClick={() =>
                  set(
                    'experience',
                    draft.experience.filter((_, i) => i !== index),
                  )
                }
              >
                Удалить место работы
              </button>
            </div>
          </div>
        ))}
        <button
          type="button"
          className="btn btn--ghost btn--small"
          onClick={() => set('experience', [jobDraft(), ...draft.experience])}
        >
          Добавить место работы
        </button>
      </fieldset>

      <Area
        label="Образование"
        hint="по одному в строке"
        value={draft.education}
        onChange={(v) => set('education', v)}
      />
      <Area
        label="Языки"
        hint="по одному в строке"
        value={draft.languages}
        onChange={(v) => set('languages', v)}
      />

      <div className="editor__actions">
        <p className="muted">
          Сохранение создаёт новую версию, прежняя остаётся в истории. Оценки, сделанные по
          прежней, получат пометку «прежнее резюме».
        </p>
        <div className="row">
          <button className="btn" type="submit" disabled={isSaving || !dirty}>
            {isSaving ? 'Сохраняю…' : 'Сохранить'}
          </button>
          <button
            className="btn btn--quiet"
            type="button"
            onClick={cancel}
            disabled={isSaving}
          >
            Отмена
          </button>
        </div>
      </div>
    </form>
  )
}

function Text({
  label,
  value,
  onChange,
  placeholder,
  inputMode,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  inputMode?: 'decimal'
}) {
  return (
    <label className="editor__field">
      <span className="eyebrow">{label}</span>
      <input
        className="input"
        value={value}
        placeholder={placeholder}
        inputMode={inputMode}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  )
}

function Area({
  label,
  hint,
  value,
  onChange,
  rows = 3,
}: {
  label: string
  hint?: string
  value: string
  onChange: (value: string) => void
  rows?: number
}) {
  return (
    <label className="editor__field">
      <span className="eyebrow">
        {label}
        {hint ? <span className="editor__hint"> · {hint}</span> : null}
      </span>
      <textarea
        className="input editor__area"
        value={value}
        rows={rows}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  )
}
