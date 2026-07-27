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

  it('keeps tablet settings and check controls inside their panels', () => {
    expect(css).toMatch(/\.integration-key-row\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto/s)
    expect(css).toMatch(/\.proxy-editor-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*\.9fr\)\s+minmax\(0,\s*1\.1fr\)/s)
    expect(css).toMatch(/@media \(min-width: 761px\) and \(max-width: 1100px\)[\s\S]*\.checks-layout\s*\{[^}]*grid-template-columns:\s*minmax\(260px,/s)
    expect(css).toMatch(/@media \(min-width: 761px\) and \(max-width: 1100px\)[\s\S]*\.table-toolbar\s*\{[^}]*flex-wrap:\s*wrap/s)
    expect(css).toMatch(/@media \(min-width: 761px\) and \(max-width: 1100px\)[\s\S]*\.check-input-panel \.btn\s*\{[^}]*white-space:\s*nowrap/s)
  })

  it('compacts the rail without hiding language selection on narrow tablets', () => {
    expect(css).toMatch(/@media \(min-width: 761px\) and \(max-width: 900px\)[\s\S]*\.app-shell\s*,\s*\.app-shell\.rail-collapsed\s*\{[^}]*grid-template-columns:\s*64px\s+minmax\(0,\s*1fr\)/s)
    expect(css).toMatch(/@media \(min-width: 761px\) and \(max-width: 900px\)[\s\S]*\.mobile-locale\s*\{[^}]*display:\s*flex/s)
  })

  it('uses the same panel grid language throughout settings', () => {
    expect(css).toMatch(/\.settings-content\s*\{[^}]*display:\s*grid/s)
    expect(css).toMatch(/\.settings-integrations-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/s)
    expect(css).toMatch(/@media \(max-width: 760px\)[\s\S]*\.settings-integrations-grid\s*\{[^}]*grid-template-columns:\s*1fr/s)
    expect(css).not.toMatch(/\.settings-nav\s*\{/s)
  })

  it('uses an internal workspace scroll fallback on unusually short screens', () => {
    expect(css).toMatch(/@media \(min-width: 761px\) and \(max-height: 560px\)[\s\S]*\.workspace\s*\{[^}]*overflow:\s*auto/s)
  })

  it('uses theme contrast tokens on solid action buttons', () => {
    expect(css).toMatch(/\.btn\.primary\s*\{[^}]*color:\s*var\(--accent-contrast\)/s)
    expect(css).toMatch(/\.btn\.danger\s*\{[^}]*color:\s*var\(--danger-contrast\)/s)
  })
})
