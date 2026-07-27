import { readFileSync } from 'node:fs'
import { URL as NodeUrl } from 'node:url'
import { describe, expect, it } from 'vitest'

const css = readFileSync(new NodeUrl('../styles.css', import.meta.url), 'utf8')

describe('UI regression contracts', () => {
  it('reserves a stable desktop height for account check results', () => {
    expect(css).toMatch(/\.check-results-panel \.panel-body\s*\{[^}]*height:\s*550px/s)
  })

  it('uses the reserved check-list height for the account input', () => {
    expect(css).toMatch(/\.check-input-panel \.form-stack\s*\{[^}]*min-height:\s*550px/s)
    expect(css).toMatch(/\.check-input-panel \.check-input\s*\{[^}]*flex:\s*1/s)
  })

  it('uses an explicit high-contrast style for the selected language', () => {
    expect(css).toMatch(/\.locale-segment button\.active\s*\{[^}]*background:\s*var\(--accent\)[^}]*color:\s*var\(--accent-contrast\)/s)
  })
})
