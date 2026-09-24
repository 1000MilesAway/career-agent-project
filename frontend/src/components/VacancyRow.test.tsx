/** Инвариант пайплайна: отсев никогда не молчит.
 *
 * Выброшенная вакансия остаётся видимой (приглушённой) и несёт причину.
 * Спрятать её без объяснения нельзя — это правило из CLAUDE.md, и здесь
 * оно проверяется на разметке, а не на намерениях.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { FoundVacancy } from '../api/types'
import { VacancyRow } from './VacancyRow'

const base: FoundVacancy = {
  id: '1',
  url: 'https://career.habr.com/vacancies/1',
  title: 'Python-разработчик',
  company: 'ООО Ромашка',
  qualification: 'Middle',
  salary_text: 'от 200 000 ₽',
  remote: true,
  source: 'habr',
  stage: 'shortlist',
  screen_reason: null,
  triage: null,
  saved: false,
  status: null,
  score: null,
  verdict: null,
  report: null,
  artifacts: null,
  rub: null,
}

describe('VacancyRow', () => {
  it('отсеянная вакансия показана, а не спрятана, и несёт причину', () => {
    render(
      <VacancyRow
        vacancy={{
          ...base,
          stage: 'screened_out',
          screen_reason: 'стоп-слово «тестировщик» в заголовке',
        }}
        actions={null}
      />,
    )

    expect(screen.getByText('Python-разработчик')).toBeTruthy()
    expect(screen.getByText('отсеяно')).toBeTruthy()
    expect(screen.getByText(/стоп-слово/)).toBeTruthy()
  })

  it('причина не теряется даже если сервер её не прислал', () => {
    render(
      <VacancyRow
        vacancy={{ ...base, stage: 'triaged_out', screen_reason: null }}
        actions={null}
      />,
    )

    // Явное «без причины» лучше пустого места: видно, что причина потерялась.
    expect(screen.getByText(/без причины/)).toBeTruthy()
  })

  it('вердикт triage виден вместе с обоснованием', () => {
    render(
      <VacancyRow
        vacancy={{
          ...base,
          triage: { bucket: 'maybe', reason: 'стек смежный, грейд выше' },
        }}
        actions={null}
      />,
    )

    expect(screen.getByText('может быть')).toBeTruthy()
    expect(screen.getByText(/стек смежный/)).toBeTruthy()
  })

  it('оценка по прежнему резюме помечена и показана с одним знаком', () => {
    render(
      <VacancyRow
        vacancy={{ ...base, score: 4, verdict: 'apply', outdated: true }}
        actions={null}
      />,
    )

    expect(screen.getByText('прежнее резюме')).toBeTruthy()
    // «4» без дробной части читается как округлённый балл рядом с «4.3».
    expect(screen.getByText('4.0')).toBeTruthy()
  })

  it('вакансия без ссылки всё равно рисуется, заголовком без якоря', () => {
    const { container } = render(
      <VacancyRow vacancy={{ ...base, url: null, source: 'telegram' }} actions={null} />,
    )

    expect(screen.getByText('Python-разработчик')).toBeTruthy()
    expect(container.querySelector('a')).toBeNull()
  })
})
