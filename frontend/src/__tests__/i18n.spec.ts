import { describe, expect, it } from 'vitest'

import { DEFAULT_LOCALE, messages, resolveLocale } from '../i18n'

function flattenKeys(value: unknown, prefix = ''): string[] {
  if (!value || typeof value !== 'object') return [prefix]
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    flattenKeys(child, prefix ? `${prefix}.${key}` : key),
  )
}

describe('i18n contract', () => {
  it('keeps vi, en and zh-CN translation keys in parity', () => {
    const expected = flattenKeys(messages.vi).sort()

    expect(flattenKeys(messages.en).sort()).toEqual(expected)
    expect(flattenKeys(messages['zh-CN']).sort()).toEqual(expected)
  })

  it('resolves saved and browser locales with Vietnamese fallback', () => {
    expect(resolveLocale('zh-CN', 'en-US')).toBe('zh-CN')
    expect(resolveLocale(null, 'en-GB')).toBe('en')
    expect(resolveLocale(null, 'zh-TW')).toBe('zh-CN')
    expect(resolveLocale(null, 'fr-FR')).toBe(DEFAULT_LOCALE)
  })
})
