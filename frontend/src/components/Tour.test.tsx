/** Подсказки первого входа: по одной, у того, о чём они, и каждая
 * заканчивается самим действием — не только кнопкой «Понятно». */

import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { Tour } from './Tour'
import { setTourStep } from './tour'

function renderAt(pathname: string, savedCount = 0) {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <Tour savedCount={savedCount} />
    </MemoryRouter>,
  )
}

const hint = (name: string) => screen.queryByRole('dialog', { name, hidden: true })

afterEach(() => act(() => setTourStep(null)))

describe('Tour', () => {
  it('says nothing to an account that never started it', () => {
    renderAt('/resume')
    expect(screen.queryByRole('dialog', { hidden: true })).toBeNull()
  })

  it('points to the search after the first CV', () => {
    act(() => setTourStep('search'))
    renderAt('/resume')
    expect(hint('Резюме готово — пора искать')).toBeTruthy()
  })

  it('moves on to the star once the search is open', () => {
    act(() => setTourStep('search'))
    renderAt('/search')
    expect(hint('Резюме готово — пора искать')).toBeNull()
    expect(hint('Понравилась вакансия?')).toBeTruthy()
  })

  it('ends when a vacancy is saved', () => {
    act(() => setTourStep('star'))
    renderAt('/search', 1)
    expect(screen.queryByRole('dialog', { hidden: true })).toBeNull()
    expect(localStorage.getItem('career-agent:tour:v1')).toBeNull()
  })

  it('ends on «Понятно»', () => {
    act(() => setTourStep('star'))
    renderAt('/search')
    fireEvent.click(screen.getByRole('button', { name: 'Понятно', hidden: true }))
    expect(screen.queryByRole('dialog', { hidden: true })).toBeNull()
  })
})
