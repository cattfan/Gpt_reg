import { beforeEach, describe, expect, it } from 'vitest'

import { createPreferences, resolveTheme } from '../composables/usePreferences'

describe('preferences', () => {
  beforeEach(() => localStorage.clear())

  it('prefers an explicit saved theme over the system preference', () => {
    expect(resolveTheme('dark', false)).toBe('dark')
    expect(resolveTheme(null, true)).toBe('dark')
    expect(resolveTheme(null, false)).toBe('light')
  })

  it('persists the active view without persisting sensitive input', () => {
    const preferences = createPreferences()

    preferences.setActiveView('checks')

    expect(localStorage.getItem('gptreg.view')).toBe('checks')
    expect(Object.keys(localStorage)).toEqual(['gptreg.view'])
  })
})
