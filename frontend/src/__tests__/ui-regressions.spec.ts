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
    expect(css).toMatch(/\.locale-control select\s*\{[^}]*background:\s*var\(--surface-muted\)[^}]*color:\s*var\(--text\)/s)
  })

  it('lets the Jobs list fill its grid row without a hard-coded max height', () => {
    expect(css).not.toMatch(/\.job-list\s*\{[^}]*max-height:\s*282px/s)
    expect(css).toMatch(/\.batch-panel\s*\{[^}]*height:\s*520px/s)
    expect(css).toMatch(/\.jobs-panel\s*\{[^}]*display:\s*flex[^}]*min-height:\s*0/s)
    expect(css).toMatch(/\.jobs-panel\s*\{[^}]*height:\s*520px/s)
    expect(css).toMatch(/\.job-list\s*\{[^}]*flex:\s*1[^}]*overflow:\s*auto/s)
  })

  it('keeps registration source controls scrollable on mobile', () => {
    expect(css).toMatch(/\.source-segment\s*\{[^}]*overflow-x:\s*auto/s)
  })

  it('uses theme contrast tokens on solid action buttons', () => {
    expect(css).toMatch(/\.btn\.primary\s*\{[^}]*color:\s*var\(--accent-contrast\)/s)
    expect(css).toMatch(/\.btn\.danger\s*\{[^}]*color:\s*var\(--danger-contrast\)/s)
  })
})
