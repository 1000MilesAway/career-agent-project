/** Онбординг после первого резюме: виден только по флагу перехода,
 * «Понятно» убирает его насовсем — и с экрана, и из истории. */

import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ONBOARDING_STATE, Onboarding } from './Onboarding'

function renderAt(state: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/search', state }]}>
      <Onboarding />
    </MemoryRouter>,
  )
}

describe('Onboarding', () => {
  it('names every menu item after the first upload', () => {
    renderAt(ONBOARDING_STATE)
    for (const item of ['Поиск', 'Избранное', 'Помощник', 'Моё резюме']) {
      expect(screen.getByText(item)).toBeTruthy()
    }
  })

  it('stays away on an ordinary visit', () => {
    const view = renderAt(null)
    expect(view.container.textContent).toBe('')
  })

  it('closes on «Понятно»', () => {
    const view = renderAt(ONBOARDING_STATE)
    fireEvent.click(screen.getByRole('button', { name: 'Понятно' }))
    expect(view.container.textContent).toBe('')
  })
})
