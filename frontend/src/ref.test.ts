import { afterEach, describe, expect, it } from 'vitest'
import { captureRef, storedRef } from './ref'

function visit(path: string) {
  history.replaceState(null, '', path)
  captureRef()
}

describe('captureRef', () => {
  afterEach(() => localStorage.clear())

  it('keeps the first label and cleans the address bar', () => {
    visit('/?ref=VC&x=1')
    expect(storedRef()).toBe('vc')
    expect(location.pathname + location.search).toBe('/?x=1')

    visit('/login?ref=habr')
    expect(storedRef()).toBe('vc')
  })

  it('falls back to utm_source', () => {
    visit('/?utm_source=telegram&utm_medium=post')
    expect(storedRef()).toBe('telegram')
    expect(location.search).toBe('')
  })

  it('ignores a label that is not one', () => {
    visit('/?ref=%3Cscript%3E')
    expect(storedRef()).toBeNull()
  })
})
