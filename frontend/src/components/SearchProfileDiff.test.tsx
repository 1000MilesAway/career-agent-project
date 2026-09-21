/** Правило подтверждений: агент предлагает, пользователь принимает.
 *
 * Эти тесты держат его на экране. Молчание — отказ, а поля, которых
 * предложение не касается, не затираются.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SearchProfileDiff } from './SearchProfileDiff'

const current = {
  queries: ['python', 'backend'],
  negative: ['QA'],
  min_qualification: 'Middle',
  // Поле, которого предложение не касается вовсе.
  salary_min: 250000,
}

const proposed = {
  queries: ['python', 'fastapi'],
  negative: ['QA', 'верстка'],
  min_qualification: 'Senior',
}

describe('SearchProfileDiff', () => {
  it('показывает, что добавится и что уберётся', () => {
    render(
      <SearchProfileDiff
        current={current}
        proposed={proposed}
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />,
    )

    expect(screen.getByText('+ fastapi')).toBeTruthy()
    expect(screen.getByText('+ верстка')).toBeTruthy()
    expect(screen.getByText('− backend')).toBeTruthy()
  })

  it('ничего не сохраняет до нажатия «Принять»', () => {
    const onAccept = vi.fn()
    render(
      <SearchProfileDiff
        current={current}
        proposed={proposed}
        onAccept={onAccept}
        onReject={vi.fn()}
      />,
    )

    expect(onAccept).not.toHaveBeenCalled()
  })

  it('не затирает поля, которых предложение не касается', async () => {
    const onAccept = vi.fn()
    render(
      <SearchProfileDiff
        current={current}
        proposed={proposed}
        onAccept={onAccept}
        onReject={vi.fn()}
      />,
    )

    screen.getByText('Принять').click()
    await vi.waitFor(() => expect(onAccept).toHaveBeenCalled())

    // salary_min не было в предложении и обязано пережить приём.
    expect(onAccept.mock.calls[0][0]).toMatchObject({
      salary_min: 250000,
      queries: ['python', 'fastapi'],
      min_qualification: 'Senior',
    })
  })

  it('когда менять нечего, «Принять» недоступно', () => {
    render(
      <SearchProfileDiff
        current={current}
        proposed={{
          queries: current.queries,
          negative: current.negative,
          min_qualification: current.min_qualification,
        }}
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />,
    )

    expect(screen.getByText('Принять').hasAttribute('disabled')).toBe(true)
    expect(screen.getByText(/Модель предложила то же самое/)).toBeTruthy()
  })
})
