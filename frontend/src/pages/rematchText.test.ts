import { describe, expect, it } from 'vitest'
import { rematchText } from './rematchText'

describe('rematchText', () => {
  it('без изменений в поиске — только первая фраза', () => {
    expect(rematchText('Профиль поиска обновлён', null)).toBe('Профиль поиска обновлён')
    expect(
      rematchText('Профиль поиска обновлён', { added: 0, removed: 0, reasons: [] }),
    ).toBe('Профиль поиска обновлён')
  })

  it('называет, сколько убрано и почему', () => {
    const text = rematchText('Профиль поиска обновлён', {
      added: 2,
      removed: 5,
      reasons: [
        { reason: 'стоп-слово в заголовке', count: 4 },
        { reason: 'не та специализация', count: 1 },
      ],
    })
    expect(text).toBe(
      'Профиль поиска обновлён. В поиске добавлено 2, убрано 5 ' +
        '(стоп-слово в заголовке — 4; не та специализация — 1).',
    )
  })
})
