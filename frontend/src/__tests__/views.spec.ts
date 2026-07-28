import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CheckAccountsView from '../views/CheckAccountsView.vue'
import RegistrationView from '../views/RegistrationView.vue'
import SettingsView from '../views/SettingsView.vue'
import { settleConfirm } from '../composables/useConfirm'
import { createAppI18n } from '../i18n'

const sseListeners = vi.hoisted(() => ({
  registration: null as ((event: Record<string, unknown>) => void) | null,
  checks: null as ((event: Record<string, unknown>) => void) | null,
}))

vi.mock('../services/sse', () => ({
  subscribeSse: (scope: 'registration' | 'checks', listener: (event: Record<string, unknown>) => void) => {
    sseListeners[scope] = listener
    return () => { sseListeners[scope] = null }
  },
}))

const ok = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), {
  status: 200,
  headers: { 'Content-Type': 'application/json' },
}))

function mountView(component: object) {
  return mount(component, { global: { plugins: [createAppI18n('vi')] } })
}

describe('operational views', () => {
  beforeEach(() => {
    settleConfirm(false)
    Object.assign(sseListeners, { registration: null, checks: null })
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/limits')) return ok({ concurrency_choices: [1, 5], max_browser: 5, max_http: 5, check_concurrency_choices: [1, 5], max_check: 5 })
      if (url.endsWith('/api/settings/integration-keys')) return ok({ 'sms.smsbower.api_key': 'sms-secret', 'accstack.api_key': 'acc-secret' })
      if (url.includes('/api/settings')) return ok({ 'reg.source': 'outlook' })
      if (url.includes('/api/mail-sources/status')) return ok({ configured: true, balance: 500, currency: 'USD', price: 50, stock: 10, affordable: 10, products: [{ id: '5', name: 'Gmail OpenAI', price: 50, stock: 10 }] })
      if (url.endsWith('/api/proxies')) return ok({ enabled: true, items: [{ id: 1, value: 'proxy.example:8000', selected: true }], selected: 1, total: 1 })
      if (url.includes('/api/sms/status')) return ok({ configured: false })
      if (url.endsWith('/api/jobs/status')) return ok({ running: false })
      if (url.endsWith('/api/jobs')) return ok([
        { id: 'job-running', email: 'account@example.com', status: 'running', reg_mode: 'http' },
        { id: 'job-error', email: 'failed@example.com', status: 'error', reg_mode: 'browser', error: 'failed' },
        { id: 'job-success', email: 'done@example.com', status: 'success', reg_mode: 'http' },
      ])
      if (url.endsWith('/api/jobs/job-error/logs')) return ok({ job_id: 'job-error', lines: ['account-specific log'] })
      if (url.endsWith('/api/jobs/retry')) return ok({ job_ids: ['job-error'] })
      if (url.endsWith('/api/jobs/clear')) return ok({ removed: 1 })
      if (url.endsWith('/api/checks')) return ok([{ id: 'check-1', email: 'account@example.com', status: 'running', has_subscription: false, mfa_enabled: false, deactivated: false }])
      if (url.endsWith('/api/checks/check-1/logs')) return ok({ check_id: 'check-1', lines: ['historical check log'] })
      if (url.includes('/api/checks/export?')) return Promise.resolve(new Response('', { status: 200 }))
      return ok([])
    }))
  })

  it('renders registration batch controls, jobs and activity', async () => {
    const wrapper = mountView(RegistrationView)
    await flushPromises()
    expect(wrapper.get('textarea').attributes('placeholder')).toContain('refresh_token')
    expect(wrapper.text()).toContain('Chạy đăng ký')
    expect(wrapper.text()).toContain('Tác vụ')
    expect(wrapper.text()).toContain('Hoạt động')
    expect(wrapper.get('button[aria-label="Xoá tất cả"]')).toBeTruthy()
    expect(wrapper.find('button button').exists()).toBe(false)
    expect(wrapper.get('[data-testid="registration-run"]').attributes('disabled')).toBeDefined()
  })

  it('switches among three registration sources and sends source-specific payloads', async () => {
    const defaultFetch = vi.mocked(fetch)
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/api/jobs') && !init?.method) return ok([])
      return defaultFetch(input, init)
    }))
    const wrapper = mountView(RegistrationView)
    await flushPromises()

    expect(wrapper.text()).toContain('Hotmail/Outlook')
    expect(wrapper.text()).toContain('Gmail (SMSBower)')
    expect(wrapper.text()).toContain('Gmail (AccStack)')

    await wrapper.get('[data-testid="source-gmail_smsbower"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('textarea[data-testid="outlook-input"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="rental-count"]')).toBeTruthy()
    expect(wrapper.text()).toContain('Số dư')
    expect(wrapper.text()).toContain('$5.00')

    await wrapper.get('[data-testid="profile-ko"]').trigger('click')
    await wrapper.get('[data-testid="rental-count"]').setValue('2')
    await wrapper.get('[data-testid="registration-run"]').trigger('click')
    await flushPromises()
    const startCall = [...vi.mocked(fetch).mock.calls].reverse().find(([url]) => String(url).endsWith('/api/jobs/start'))
    const body = JSON.parse(String(startCall?.[1]?.body))
    expect(body).toMatchObject({ source: 'gmail_smsbower', rental_count: 2, profile_region: 'ko' })
    expect(body).not.toHaveProperty('input')
  })

  it('only renders the AccStack product selector when multiple products are available', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/limits')) return ok({ concurrency_choices: [1], max_browser: 1, max_http: 1, check_concurrency_choices: [1], max_check: 1 })
      if (url.includes('/api/settings')) return ok({ 'reg.source': 'gmail_accstack' })
      if (url.includes('/api/mail-sources/status')) return ok({
        configured: true, balance: 1000, currency: 'USD', price: 50, stock: 20, affordable: 20,
        products: [{ id: '5', name: 'Gmail A', price: 50, stock: 10 }, { id: '6', name: 'Gmail B', price: 60, stock: 10 }],
      })
      if (url.endsWith('/api/jobs')) return ok([])
      return ok([])
    }))
    const wrapper = mountView(RegistrationView)
    await flushPromises()
    expect(wrapper.get('[data-testid="accstack-product"]').findAll('option')).toHaveLength(2)
  })

  it('keeps a Gmail rental batch locked until the manager reports idle', async () => {
    const defaultFetch = vi.mocked(fetch)
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/jobs/start')) return ok({ rental_ids: ['rental-1'], rental_count: 1 })
      if (url.endsWith('/api/jobs/status')) return ok({ running: true })
      if (url.endsWith('/api/jobs') && !init?.method) return ok([])
      return defaultFetch(input, init)
    }))
    const wrapper = mountView(RegistrationView)
    await flushPromises()
    await wrapper.get('[data-testid="source-gmail_smsbower"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="registration-run"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="registration-run"]').attributes('disabled')).toBeDefined()
    sseListeners.registration?.({ type: 'batch', status: 'idle' })
    await flushPromises()
    expect(wrapper.get('[data-testid="registration-run"]').attributes('disabled')).toBeUndefined()
  })

  it('unlocks a Gmail batch from runtime status when the idle SSE event is missed', async () => {
    const defaultFetch = vi.mocked(fetch)
    let runtimeRunning = false
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/jobs/start')) {
        runtimeRunning = true
        return ok({ rental_ids: ['rental-1'], rental_count: 1 })
      }
      if (url.endsWith('/api/jobs/status')) return ok({ running: runtimeRunning })
      if (url.endsWith('/api/jobs') && !init?.method) return ok([])
      return defaultFetch(input, init)
    }))
    const wrapper = mountView(RegistrationView)
    await flushPromises()
    await wrapper.get('[data-testid="source-gmail_smsbower"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="registration-run"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="registration-run"]').attributes('disabled')).toBeDefined()

    runtimeRunning = false
    sseListeners.registration?.({ type: 'job', status: 'success' })
    await new Promise((resolve) => window.setTimeout(resolve, 350))
    await flushPromises()
    expect(wrapper.get('[data-testid="registration-run"]').attributes('disabled')).toBeUndefined()
  })

  it('retries and deletes an individual terminal account', async () => {
    const wrapper = mountView(RegistrationView)
    await flushPromises()

    await wrapper.get('[data-testid="job-retry-job-error"]').trigger('click')
    await flushPromises()
    const retryCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/jobs/retry'))
    expect(JSON.parse(String(retryCall?.[1]?.body))).toMatchObject({ job_ids: ['job-error'] })

    await wrapper.get('[data-testid="job-delete-job-success"]').trigger('click')
    settleConfirm(true)
    await flushPromises()
    const deleteCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/jobs/clear'))
    expect(JSON.parse(String(deleteCall?.[1]?.body))).toEqual({ job_ids: ['job-success'] })
  })

  it('shows only the selected account log and hides it after clicking elsewhere', async () => {
    const wrapper = mountView(RegistrationView)
    await flushPromises()

    await wrapper.get('[data-testid="job-select-job-error"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('account-specific log')

    await wrapper.get('[data-testid="job-retry-job-error"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('account-specific log')

    await wrapper.get('[data-testid="job-select-job-error"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('account-specific log')

    await wrapper.get('[data-testid="registration-view"]').trigger('click')
    expect(wrapper.text()).not.toContain('account-specific log')
    expect(wrapper.find('.job-row.selected').exists()).toBe(false)
  })

  it('copies registration log without closing the selected job', async () => {
    const wrapper = mountView(RegistrationView)
    await flushPromises()
    await wrapper.get('[data-testid="job-select-job-error"]').trigger('click')
    await flushPromises()

    await wrapper.get('[data-testid="registration-copy-log"]').trigger('click')
    await flushPromises()
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('account-specific log')
    expect(wrapper.find('.job-row.selected').exists()).toBe(true)
  })

  it('sends an explicit opt-in engine fallback for start and retry', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/limits')) return ok({ concurrency_choices: [1, 10, 50], max_browser: 10, max_http: 200, check_concurrency_choices: [1], max_check: 1 })
      if (url.includes('/api/settings')) return ok({ 'reg.source': 'outlook' })
      if (url.endsWith('/api/proxies')) return ok({ enabled: true, items: [{ id: 1, value: 'proxy.example:8000', selected: true }], selected: 1, total: 1 })
      if (url.endsWith('/api/jobs')) return ok([
        { id: 'job-error', email: 'failed@example.com', status: 'error', reg_mode: 'http', error: 'failed' },
      ])
      if (url.endsWith('/api/jobs/start')) return ok({ job_ids: ['new-job'] })
      if (url.endsWith('/api/jobs/retry')) return ok({ job_ids: ['job-error'] })
      return ok([])
    }))
    const wrapper = mountView(RegistrationView)
    await flushPromises()

    const fallback = wrapper.find('[data-testid="engine-fallback"]')
    expect(fallback.exists()).toBe(true)
    expect((fallback.element as HTMLInputElement).checked).toBe(false)

    await wrapper.get('textarea').setValue('mail@example.com|pass|refresh|client')
    await wrapper.get('[data-testid="registration-run"]').trigger('click')
    await flushPromises()
    const startCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/jobs/start'))
    expect(JSON.parse(String(startCall?.[1]?.body))).toMatchObject({ fallback_enabled: false })

    await fallback.setValue(true)
    await wrapper.get('[data-testid="job-retry-job-error"]').trigger('click')
    await flushPromises()
    const retryCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/jobs/retry'))
    expect(JSON.parse(String(retryCall?.[1]?.body))).toMatchObject({ fallback_enabled: true })
  })

  it('persists registration proxy rows and uses the selected pool for status, start and retry', async () => {
    let proxyItems = [
      { id: 1, value: 'one.example:8001', selected: true },
      { id: 2, value: 'two.example:8002', selected: false },
    ]
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/limits')) return ok({ concurrency_choices: [1], max_browser: 1, max_http: 1, check_concurrency_choices: [1], max_check: 1 })
      if (url.includes('/api/settings')) return ok({ 'reg.source': 'outlook' })
      if (url.endsWith('/api/proxies') && init?.method === 'PUT') {
        proxyItems = JSON.parse(String(init.body)).items
        return ok({ enabled: true, items: proxyItems, selected: proxyItems.filter((item) => item.selected).length, total: proxyItems.length })
      }
      if (url.endsWith('/api/proxies')) return ok({ enabled: true, items: proxyItems, selected: 1, total: 2 })
      if (url.endsWith('/api/jobs/status')) return ok({ running: false })
      if (url.endsWith('/api/jobs')) return ok([{ id: 'job-error', email: 'failed@example.com', status: 'error', error: 'failed' }])
      if (url.includes('/api/mail-sources/status')) return ok({ configured: true, balance: 500, currency: 'USD', price: 50, stock: 10, affordable: 10, products: [] })
      if (url.endsWith('/api/jobs/start')) return ok({ job_ids: ['new-job'] })
      if (url.endsWith('/api/jobs/retry')) return ok({ job_ids: ['job-error'] })
      return ok([])
    }))
    const wrapper = mountView(RegistrationView)
    await flushPromises()

    const firstProxy = wrapper.get('[data-testid="registration-proxy-selected-0"]')
    const secondProxy = wrapper.get('[data-testid="registration-proxy-selected-1"]')
    expect((firstProxy.element as HTMLInputElement).checked).toBe(true)
    expect(firstProxy.attributes('disabled')).toBeDefined()
    expect((secondProxy.element as HTMLInputElement).checked).toBe(false)
    expect(wrapper.find('[data-testid="registration-proxy-enabled"]').exists()).toBe(false)

    await secondProxy.setValue(true)
    await flushPromises()
    expect(firstProxy.attributes('disabled')).toBeUndefined()
    await firstProxy.setValue(false)
    await flushPromises()
    expect(secondProxy.attributes('disabled')).toBeDefined()
    const proxyCalls = vi.mocked(fetch).mock.calls.filter(([url, options]) => String(url).endsWith('/api/proxies') && options?.method === 'PUT')
    expect(JSON.parse(String(proxyCalls.at(-1)?.[1]?.body))).toEqual({
      items: [
        { value: 'one.example:8001', selected: false },
        { value: 'two.example:8002', selected: true },
      ],
    })

    await wrapper.get('[data-testid="source-gmail_smsbower"]').trigger('click')
    await flushPromises()
    const statusCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).includes('/api/mail-sources/status'))
    expect(String(statusCall?.[0])).toContain('source=gmail_smsbower')
    expect(String(statusCall?.[0])).not.toContain('proxy_enabled')

    await wrapper.get('[data-testid="source-outlook"]').trigger('click')
    await wrapper.get('[data-testid="outlook-input"]').setValue('mail@example.com|pass|refresh|client')
    await wrapper.get('[data-testid="registration-run"]').trigger('click')
    await flushPromises()
    const startCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/jobs/start'))
    expect(JSON.parse(String(startCall?.[1]?.body))).not.toHaveProperty('proxy_enabled')

    await wrapper.get('[data-testid="job-retry-job-error"]').trigger('click')
    await flushPromises()
    const retryCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/jobs/retry'))
    expect(JSON.parse(String(retryCall?.[1]?.body))).not.toHaveProperty('proxy_enabled')
  })

  it('formats AccStack balance and price with its currency divisor', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/limits')) return ok({ concurrency_choices: [1], max_browser: 1, max_http: 1, check_concurrency_choices: [1], max_check: 1 })
      if (url.includes('/api/settings')) return ok({ 'reg.source': 'gmail_accstack' })
      if (url.endsWith('/api/proxies')) return ok({ enabled: false, items: [], selected: 0, total: 0 })
      if (url.includes('/api/mail-sources/status')) return ok({
        configured: true, balance: 4895, currency: 'USD', currency_divisor: 1000,
        price: 34, stock: 237, affordable: 143, products: [{ id: '5', name: 'Gmail', price: 34, stock: 237 }],
      })
      if (url.endsWith('/api/jobs/status')) return ok({ running: false })
      if (url.endsWith('/api/jobs')) return ok([])
      return ok([])
    }))
    const registration = mountView(RegistrationView)
    await flushPromises()

    expect(registration.text()).toContain('$4.895')
    expect(registration.text()).toContain('$0.034')

    const settings = mountView(SettingsView)
    await flushPromises()
    expect(settings.text()).toContain('$4.895')
    expect(settings.text()).toContain('$0.034')
  })

  it('renders searchable account check results', async () => {
    const wrapper = mountView(CheckAccountsView)
    await flushPromises()
    expect(wrapper.get('input[type="search"]')).toBeTruthy()
    expect(wrapper.text()).toContain('Check plan')
    expect(wrapper.find('table').exists()).toBe(true)
    expect(wrapper.get('[data-testid="checks-run"]').attributes('disabled')).toBeDefined()
  })

  it('loads, streams, copies and closes the selected account check log', async () => {
    const wrapper = mountView(CheckAccountsView)
    await flushPromises()

    await wrapper.get('[data-testid="check-select-check-1"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('historical check log')
    expect(wrapper.find('tr.selected').exists()).toBe(true)

    sseListeners.checks?.({ type: 'check_log', scope: 'check', check_id: 'other', line: 'wrong log' })
    sseListeners.checks?.({ type: 'check_log', scope: 'check', check_id: 'check-1', line: 'realtime check log' })
    await flushPromises()
    expect(wrapper.text()).not.toContain('wrong log')
    expect(wrapper.text()).toContain('realtime check log')

    await wrapper.get('[data-testid="check-copy-log"]').trigger('click')
    await flushPromises()
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('historical check log\nrealtime check log')
    expect(wrapper.find('tr.selected').exists()).toBe(true)

    await wrapper.get('[data-testid="checks-view"]').trigger('click')
    expect(wrapper.find('tr.selected').exists()).toBe(false)
  })

  it('groups live Free and Plus accounts into quick-copy outputs', async () => {
    const defaultFetch = vi.mocked(fetch)
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/api/checks') && !init?.method) return ok([
        { id: 'free-1', email: 'free@example.com', status: 'live', plan: 'free', has_subscription: false, mfa_enabled: false, deactivated: false },
        { id: 'plus-1', email: 'plus@example.com', status: 'live', plan: 'plus', has_subscription: true, mfa_enabled: true, deactivated: false },
        { id: 'error-1', email: 'error@example.com', status: 'error', has_subscription: false, mfa_enabled: false, deactivated: false },
      ])
      if (String(input).includes('/api/checks/export?status=live&plan=free&fmt=combo')) {
        return Promise.resolve(new Response('free@example.com|FreePass|FREE2FA\n', { status: 200 }))
      }
      if (String(input).includes('/api/checks/export?status=live&plan=plus&fmt=combo')) {
        return Promise.resolve(new Response('plus@example.com|PlusPass|PLUS2FA\n', { status: 200 }))
      }
      return defaultFetch(input, init)
    }))
    const wrapper = mountView(CheckAccountsView)
    await flushPromises()

    expect((wrapper.get('[data-testid="free-accounts-output"]').element as HTMLTextAreaElement).value).toBe('free@example.com|FreePass|FREE2FA')
    expect((wrapper.get('[data-testid="plus-accounts-output"]').element as HTMLTextAreaElement).value).toBe('plus@example.com|PlusPass|PLUS2FA')
    await wrapper.get('[data-testid="copy-free-accounts"]').trigger('click')
    await wrapper.get('[data-testid="copy-plus-accounts"]').trigger('click')
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('free@example.com|FreePass|FREE2FA')
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('plus@example.com|PlusPass|PLUS2FA')
  })

  it('persists check proxy rows and uses the selected pool for start and retry', async () => {
    let proxyItems = [
      { id: 1, value: 'one.example:8001', selected: true },
      { id: 2, value: 'two.example:8002', selected: false },
    ]
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/limits')) return ok({ concurrency_choices: [1], max_browser: 1, max_http: 1, check_concurrency_choices: [1], max_check: 1 })
      if (url.endsWith('/api/proxies') && init?.method === 'PUT') {
        proxyItems = JSON.parse(String(init.body)).items
        return ok({ enabled: true, items: proxyItems, selected: proxyItems.filter((item) => item.selected).length, total: proxyItems.length })
      }
      if (url.endsWith('/api/proxies')) return ok({ enabled: true, items: proxyItems, selected: 1, total: 2 })
      if (url.endsWith('/api/checks/start')) return ok({ check_ids: ['check-1'] })
      if (url.endsWith('/api/checks/retry')) return ok({ check_ids: ['check-2'] })
      if (url.endsWith('/api/checks')) return ok([])
      return ok([])
    }))
    const wrapper = mountView(CheckAccountsView)
    await flushPromises()

    const firstProxy = wrapper.get('[data-testid="checks-proxy-selected-0"]')
    const secondProxy = wrapper.get('[data-testid="checks-proxy-selected-1"]')
    expect(firstProxy.attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="checks-proxy-enabled"]').exists()).toBe(false)

    await secondProxy.setValue(true)
    await flushPromises()
    await firstProxy.setValue(false)
    await flushPromises()
    expect(secondProxy.attributes('disabled')).toBeDefined()
    const proxyCall = vi.mocked(fetch).mock.calls.filter(([url, options]) => String(url).endsWith('/api/proxies') && options?.method === 'PUT').at(-1)
    expect(JSON.parse(String(proxyCall?.[1]?.body))).toEqual({
      items: [
        { value: 'one.example:8001', selected: false },
        { value: 'two.example:8002', selected: true },
      ],
    })

    await wrapper.get('textarea').setValue('mail@example.com|pass|2fa')
    await wrapper.get('[data-testid="checks-run"]').trigger('click')
    await flushPromises()
    const startCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/checks/start'))
    expect(JSON.parse(String(startCall?.[1]?.body))).not.toHaveProperty('proxy_enabled')

    await wrapper.get('.check-results-panel .panel-actions button').trigger('click')
    await flushPromises()
    const retryCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/checks/retry'))
    expect(JSON.parse(String(retryCall?.[1]?.body))).not.toHaveProperty('proxy_enabled')
  })

  it('renders integrations and selectable random proxy settings without Appearance', async () => {
    const wrapper = mountView(SettingsView)
    await flushPromises()
    expect(wrapper.get('[data-testid="smsbower-api-key"]').attributes('type')).toBe('text')
    expect(wrapper.get('[data-testid="accstack-api-key"]').attributes('type')).toBe('text')
    expect(wrapper.find('[data-testid="proxy-enabled"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="settings-section-nav"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid^="integration-card-"]')).toHaveLength(2)
    expect(wrapper.get('[data-testid="settings-integrations-grid"]').classes()).toContain('settings-integrations-grid')
    expect(wrapper.text()).toContain('Tích hợp')
    expect(wrapper.text()).toContain('SMSBower')
    expect(wrapper.text()).toContain('AccStack')
    expect(wrapper.text()).toContain('Proxy')
    expect(wrapper.text()).not.toContain('Giao diện')
    expect(wrapper.text()).not.toContain('Round robin')
  })

  it('loads raw API keys visibly and the eye only toggles their visibility', async () => {
    const wrapper = mountView(SettingsView)
    await flushPromises()

    const input = wrapper.get('[data-testid="smsbower-api-key"]')
    const toggle = wrapper.get('[data-testid="smsbower-api-key-toggle"]')
    expect((input.element as HTMLInputElement).value).toBe('sms-secret')
    expect(input.attributes('type')).toBe('text')
    expect(toggle.attributes('disabled')).toBeUndefined()
    await toggle.trigger('click')
    expect(input.attributes('type')).toBe('password')
    expect((input.element as HTMLInputElement).value).toBe('sms-secret')
    await toggle.trigger('click')
    await input.setValue('updated-secret')
    await wrapper.get('[data-testid="integration-card-smsbower"] form').trigger('submit')
    await flushPromises()
    expect((input.element as HTMLInputElement).value).toBe('updated-secret')
    const saveCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/api/settings') && init?.method === 'POST')
    expect(JSON.parse(String(saveCall?.[1]?.body))).toEqual({ 'sms.smsbower.api_key': 'updated-secret' })
  })

  it('saves proxy rows without a global toggle and locks the final selected row', async () => {
    const wrapper = mountView(SettingsView)
    await flushPromises()

    expect(wrapper.find('[data-testid="proxy-enabled"]').exists()).toBe(false)
    await wrapper.get('[data-testid="proxy-editor"]').setValue('one.example:8001\ntwo.example:8002')
    await wrapper.get('[data-testid="proxy-selected-0"]').setValue(false)
    expect(wrapper.text()).toContain('1 / 2')
    expect(wrapper.get('[data-testid="proxy-selected-1"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-testid="proxy-save"]').trigger('click')
    await flushPromises()

    const proxyCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/api/proxies') && init?.method === 'PUT')
    expect(JSON.parse(String(proxyCall?.[1]?.body))).toEqual({
      items: [
        { value: 'one.example:8001', selected: false },
        { value: 'two.example:8002', selected: true },
      ],
    })
  })

  it('shows a proxy validation error on the matching line', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/settings')) return ok({})
      if (url.includes('/api/mail-sources/status')) return ok({ configured: false, balance: 0, currency: 'USD', price: 0, stock: 0, affordable: 0, products: [] })
      if (url.endsWith('/api/proxies') && init?.method === 'PUT') return Promise.resolve(new Response(JSON.stringify({ detail: { line: 2, message: 'bad proxy' } }), { status: 400, headers: { 'Content-Type': 'application/json' } }))
      if (url.endsWith('/api/proxies')) return ok({ enabled: false, items: [], selected: 0, total: 0 })
      return ok([])
    }))
    const wrapper = mountView(SettingsView)
    await flushPromises()

    await wrapper.get('[data-testid="proxy-editor"]').setValue('one.example:8001\nbad')
    await wrapper.get('[data-testid="proxy-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="proxy-line-error-2"]').text()).toContain('bad proxy')
  })
})
