# Single-Viewport Operations Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Giữ toàn bộ vùng chức năng chính trong một viewport trên desktop/tablet, đồng thời duy trì bố cục mobile dễ đọc với các vùng dài cuộn nội bộ.

**Architecture:** Chỉ thay đổi CSS layout hiện có. App shell trở thành khung `100dvh` từ 761px; từng view dùng grid hai hàng và các panel dùng flex/overflow nội bộ. Mobile reset chiều cao viewport, xếp dọc panel và đặt chiều cao giới hạn cho Jobs, bảng, output và log.

**Tech Stack:** Vue 3, Tailwind CSS 4, CSS Grid/Flexbox, Vitest, Playwright CLI, Vite.

---

## File Map

- Modify: `frontend/src/styles.css` - viewport shell, view grids, compact spacing, internal overflow và responsive rules.
- Modify: `frontend/src/__tests__/ui-regressions.spec.ts` - contract CSS cho viewport/grid/mobile.
- Regenerate: `gpt_reg/web/static/app/index.html`
- Regenerate: `gpt_reg/web/static/app/assets/index-*.css`
- Regenerate: `gpt_reg/web/static/app/assets/index-*.js`

### Task 1: Add Failing Viewport Layout Contracts

**Files:**
- Modify: `frontend/src/__tests__/ui-regressions.spec.ts`
- Test: `frontend/src/__tests__/ui-regressions.spec.ts`

- [ ] **Step 1: Replace the fixed 520px contract with viewport contracts**

Add assertions that require:

```ts
it('keeps desktop operations inside the viewport shell', () => {
  expect(css).toMatch(/@media \(min-width:\s*761px\)[\s\S]*\.app-shell[^}]*height:\s*100dvh/)
  expect(css).toMatch(/@media \(min-width:\s*761px\)[\s\S]*\.view-host[^}]*overflow:\s*hidden/)
  expect(css).toMatch(/\.registration-workspace\s*\{[^}]*grid-template-rows:\s*58px\s+minmax\(0,\s*1fr\)/s)
  expect(css).not.toMatch(/\.batch-panel\s*\{[^}]*height:\s*520px/s)
  expect(css).not.toMatch(/\.jobs-panel\s*\{[^}]*height:\s*520px/s)
})

it('keeps check outputs and logs visible in a second grid row', () => {
  expect(css).toMatch(/\.checks-layout\s*\{[^}]*grid-template-areas:[^}]*input results[^}]*accounts activity/s)
  expect(css).toMatch(/\.check-account-groups\s*\{[^}]*grid-area:\s*accounts/s)
  expect(css).toMatch(/\.check-activity-panel\s*\{[^}]*grid-area:\s*activity/s)
})

it('bounds long mobile panels without shrinking text', () => {
  expect(css).toMatch(/@media \(max-width:\s*760px\)[\s\S]*\.jobs-panel[^}]*height:\s*min\(52dvh,\s*520px\)/)
  expect(css).toMatch(/@media \(max-width:\s*760px\)[\s\S]*\.check-results-panel \.panel-body[^}]*max-height:\s*62dvh/)
})
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```powershell
cd frontend
npm test -- --run src/__tests__/ui-regressions.spec.ts
```

Expected: FAIL because the shell still uses `min-height: 100vh`, Registration still has `520px` panel heights, and Check has no named grid areas.

- [ ] **Step 3: Commit the failing contract**

```powershell
git add frontend/src/__tests__/ui-regressions.spec.ts
git commit -m "test: define single viewport workspace"
```

### Task 2: Implement Desktop and Tablet Viewport Grids

**Files:**
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/__tests__/ui-regressions.spec.ts`

- [ ] **Step 1: Convert the app shell to a bounded desktop frame**

Add a desktop/tablet media block with these rules:

```css
@media (min-width: 761px) {
  html, body, #app { height: 100%; overflow: hidden; }
  .app-shell { height: 100dvh; min-height: 0; overflow: hidden; }
  .app-main {
    display: grid;
    height: 100dvh;
    min-height: 0;
    grid-template-rows: 58px minmax(0, 1fr);
    overflow: hidden;
  }
  .app-topbar { min-height: 58px; padding: 7px 18px; }
  .view-host { min-height: 0; overflow: hidden; }
  .workspace { height: 100%; min-height: 0; overflow: hidden; padding: 10px 14px 12px; }
}
```

- [ ] **Step 2: Replace fixed Registration heights with a two-row grid**

Use:

```css
.registration-workspace,
.checks-workspace {
  display: grid;
  min-height: 0;
  grid-template-rows: 58px minmax(0, 1fr);
  gap: 10px;
}
.registration-grid {
  min-height: 0;
  margin-top: 0;
  grid-template-rows: minmax(260px, 1.55fr) minmax(145px, .72fr);
  gap: 10px;
}
.batch-panel,
.jobs-panel,
.activity-panel,
.results-panel {
  display: flex;
  height: auto;
  min-height: 0;
  flex-direction: column;
}
.batch-panel .panel-body,
.jobs-panel .panel-body,
.activity-panel .panel-body,
.results-panel .panel-body {
  min-height: 0;
  flex: 1;
}
```

Set Batch, Jobs, Activity, log and Results output containers to `overflow: auto` without changing font sizes.

- [ ] **Step 3: Reflow Check Accounts into two visible rows**

Use named areas:

```css
.checks-layout {
  min-height: 0;
  margin-top: 0;
  grid-template-columns: minmax(250px, .58fr) minmax(0, 1.42fr);
  grid-template-rows: minmax(260px, 1.45fr) minmax(145px, .72fr);
  grid-template-areas:
    "input results"
    "accounts activity";
  gap: 10px;
}
.check-input-panel { grid-area: input; }
.check-results-panel { grid-area: results; }
.check-account-groups { grid-area: accounts; grid-column: auto; }
.check-activity-panel { grid-area: activity; grid-column: auto; }
```

Make the input form, results body, account outputs and log fill their grid cells with internal overflow.

- [ ] **Step 4: Bound Settings inside the view host**

Keep the existing navigation column and apply:

```css
.settings-workspace { height: 100%; min-height: 0; overflow: hidden; }
.settings-layout { height: 100%; min-height: 0; gap: 10px; }
.settings-content { min-height: 0; overflow: auto; gap: 10px; }
@media (min-width: 901px) {
  .integration-body {
    display: grid;
    grid-template-columns: minmax(0, 1.15fr) minmax(260px, .85fr);
    align-items: end;
  }
}
```

Reduce panel header, integration and proxy spacing only where needed to fit a 768px-tall viewport; keep current font sizes and minimum button heights.

- [ ] **Step 5: Run the focused test and confirm GREEN**

Run:

```powershell
cd frontend
npm test -- --run src/__tests__/ui-regressions.spec.ts
```

Expected: all UI regression contracts pass.

- [ ] **Step 6: Run all frontend tests**

Run:

```powershell
npm test -- --run
```

Expected: all frontend tests pass.

- [ ] **Step 7: Commit the desktop/tablet layout**

```powershell
git add frontend/src/styles.css
git commit -m "feat: fit operations into viewport"
```

### Task 3: Implement Mobile Internal Scrolling

**Files:**
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/__tests__/ui-regressions.spec.ts`

- [ ] **Step 1: Reset the bounded shell below 761px**

Inside the existing mobile media query, set:

```css
html, body, #app { height: auto; overflow: visible; }
.app-shell, .app-shell.rail-collapsed { min-height: 100dvh; padding-bottom: 64px; }
.app-main, .view-host, .workspace { height: auto; min-height: 0; overflow: visible; }
.registration-workspace, .checks-workspace { display: block; }
```

- [ ] **Step 2: Bound mobile data-heavy panels**

Apply:

```css
.jobs-panel { height: min(52dvh, 520px); }
.job-list { overscroll-behavior: contain; }
.check-results-panel .panel-body { max-height: 62dvh; overflow: hidden; }
.check-results-panel .table-wrap { overflow: auto; }
.check-account-columns { min-height: 0; }
.account-group-output { height: 96px; }
.check-activity-panel .panel-body,
.check-log-empty { min-height: 160px; }
```

Keep the existing single-column ordering and bottom navigation padding.

- [ ] **Step 3: Run the focused test and full suite**

Run:

```powershell
cd frontend
npm test -- --run src/__tests__/ui-regressions.spec.ts
npm test -- --run
```

Expected: all tests pass.

- [ ] **Step 4: Commit mobile behavior**

```powershell
git add frontend/src/styles.css
git commit -m "fix: bound mobile operation panels"
```

### Task 4: Visual QA, Build and Delivery

**Files:**
- Regenerate: `gpt_reg/web/static/app/index.html`
- Regenerate: `gpt_reg/web/static/app/assets/index-*.css`
- Regenerate: `gpt_reg/web/static/app/assets/index-*.js`

- [ ] **Step 1: Build the production frontend**

Run:

```powershell
cd frontend
npm run build
```

Expected: Vue type-check and Vite build succeed.

- [ ] **Step 2: Restart the local server and verify the production asset**

Restart `start.bat` on `127.0.0.1:2023`, then verify root returns HTTP 200, `Cache-Control: no-store`, and references the newly generated JavaScript asset.

- [ ] **Step 3: Verify desktop and tablet with Playwright**

For Registration, Check and Settings at 1440x900 and 1024x768, assert:

```js
({
  documentHeight: document.documentElement.scrollHeight,
  viewportHeight: innerHeight,
  horizontalOverflow: document.documentElement.scrollWidth - innerWidth
})
```

Expected: `documentHeight === viewportHeight` and `horizontalOverflow === 0`. Confirm Jobs/table/log have `scrollHeight >= clientHeight` where content is long.

- [ ] **Step 4: Verify mobile with Playwright**

At 390x844, capture all three views and assert `scrollWidth === innerWidth`. Confirm Jobs and Check results scroll internally, bottom navigation does not hide the last control, and Vietnamese/English/Chinese labels fit.

- [ ] **Step 5: Run final verification**

Run:

```powershell
cd frontend
npm test -- --run
npm run build
cd ..
.\.venv311\Scripts\python.exe test\run_all.py
git diff --check
```

Expected: frontend tests, build, 44 backend checks and whitespace validation all pass.

- [ ] **Step 6: Commit generated assets and push**

```powershell
git add gpt_reg/web/static/app frontend/src/styles.css frontend/src/__tests__/ui-regressions.spec.ts
git commit -m "build: publish compact operations workspace"
git push origin main
```
