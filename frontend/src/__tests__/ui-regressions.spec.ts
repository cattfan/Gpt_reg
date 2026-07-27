import { readFileSync } from 'node:fs'
import { URL as NodeUrl } from 'node:url'
import { describe, expect, it } from 'vitest'

const css = readFileSync(new NodeUrl('../styles.css', import.meta.url), 'utf8')

describe('UI regression contracts', () => {
  it('keeps desktop operations inside the viewport shell', () => {
    expect(css).toContain('@media (min-width: 761px)')
    expect(css).toMatch(/\.app-shell\s*\{[^}]*height:\s*100dvh/s)
    expect(css).toMatch(/\.view-host\s*\{[^}]*overflow:\s*hidden/s)
    expect(css).toMatch(/\.registration-workspace\s*,\s*\.checks-workspace\s*\{[^}]*grid-template-rows:\s*58px\s+minmax\(0,\s*1fr\)/s)
    expect(css).not.toMatch(/\.batch-panel\s*\{[^}]*height:\s*520px/s)
    expect(css).not.toMatch(/\.jobs-panel\s*\{[^}]*height:\s*520px/s)
  })

  it('uses an explicit high-contrast style for the selected language', () => {
    expect(css).toMatch(/\.locale-control select\s*\{[^}]*background:\s*var\(--surface-muted\)[^}]*color:\s*var\(--text\)/s)
  })

  it('lets the Jobs list fill its viewport grid row', () => {
    expect(css).not.toMatch(/\.job-list\s*\{[^}]*max-height:\s*282px/s)
    expect(css).toMatch(/\.jobs-panel\s*\{[^}]*display:\s*flex[^}]*min-height:\s*0/s)
    expect(css).toMatch(/\.job-list\s*\{[^}]*flex:\s*1[^}]*overflow:\s*auto/s)
  })

  it('keeps check outputs and logs visible in a second grid row', () => {
    expect(css).toMatch(/\.checks-layout\s*\{[^}]*grid-template-areas:[^}]*input results[^}]*accounts activity/s)
    expect(css).toMatch(/\.check-account-groups\s*\{[^}]*grid-area:\s*accounts/s)
    expect(css).toMatch(/\.check-activity-panel\s*\{[^}]*grid-area:\s*activity/s)
  })

  it('keeps registration source controls scrollable on mobile', () => {
    expect(css).toMatch(/\.source-segment\s*\{[^}]*overflow-x:\s*auto/s)
  })

  it('bounds long mobile panels without shrinking text', () => {
    expect(css).toContain('@media (max-width: 760px)')
    expect(css).toMatch(/\.jobs-panel\s*\{[^}]*height:\s*min\(52dvh,\s*520px\)/s)
    expect(css).toMatch(/\.check-results-panel \.panel-body\s*\{[^}]*max-height:\s*62dvh/s)
  })

  it('uses theme contrast tokens on solid action buttons', () => {
    expect(css).toMatch(/\.btn\.primary\s*\{[^}]*color:\s*var\(--accent-contrast\)/s)
    expect(css).toMatch(/\.btn\.danger\s*\{[^}]*color:\s*var\(--danger-contrast\)/s)
  })
})
