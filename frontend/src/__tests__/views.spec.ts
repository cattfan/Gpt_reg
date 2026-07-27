import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CheckAccountsView from '../views/CheckAccountsView.vue'
import RegistrationView from '../views/RegistrationView.vue'
import SettingsView from '../views/SettingsView.vue'
import { settleConfirm } from '../composables/useConfirm'
import { createAppI18n } from '../i18n'

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
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/limits')) return ok({ concurrency_choices: [1, 5], max_browser: 5, max_http: 5, check_concurrency_choices: [1, 5], max_check: 5 })
      if (url.includes('/api/settings')) return ok({ 'proxy.pool': '', 'proxy.rotation_mode': 'round_robin' })
      if (url.includes('/api/sms/status')) return ok({ configured: false })
      if (url.endsWith('/api/jobs')) return ok([
        { id: 'job-running', email: 'account@example.com', status: 'running', reg_mode: 'http' },
        { id: 'job-error', email: 'failed@example.com', status: 'error', reg_mode: 'browser', error: 'failed' },
        { id: 'job-success', email: 'done@example.com', status: 'success', reg_mode: 'http' },
      ])
      if (url.endsWith('/api/jobs/job-error/logs')) return ok({ job_id: 'job-error', lines: ['account-specific log'] })
      if (url.endsWith('/api/jobs/retry')) return ok({ job_ids: ['job-error'] })
      if (url.endsWith('/api/jobs/clear')) return ok({ removed: 1 })
      if (url.endsWith('/api/checks')) return ok([{ id: 'check-1', email: 'account@example.com', status: 'running', has_subscription: false, mfa_enabled: false, deactivated: false }])
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

  it('sends an explicit opt-in engine fallback for start and retry', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/limits')) return ok({ concurrency_choices: [1, 10, 50], max_browser: 10, max_http: 200, check_concurrency_choices: [1], max_check: 1 })
      if (url.includes('/api/settings')) return ok({ 'reg.source': 'outlook' })
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

  it('renders searchable account check results', async () => {
    const wrapper = mountView(CheckAccountsView)
    await flushPromises()
    expect(wrapper.get('input[type="search"]')).toBeTruthy()
    expect(wrapper.text()).toContain('Check plan')
    expect(wrapper.find('table').exists()).toBe(true)
    expect(wrapper.get('[data-testid="checks-run"]').attributes('disabled')).toBeDefined()
  })

  it('keeps sensitive SMS input as a password field', async () => {
    const wrapper = mountView(SettingsView)
    await flushPromises()
    expect(wrapper.get('input[type="password"]')).toBeTruthy()
    expect(wrapper.text()).toContain('Proxy')
    expect(wrapper.text()).toContain('Giao diện')
  })
})
