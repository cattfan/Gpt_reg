import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import App from '../App.vue'
import { createAppI18n } from '../i18n'

function mountApp() {
  return mount(App, { global: { plugins: [createAppI18n('vi')] } })
}

describe('application shell', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('gptreg.locale', 'vi')
    document.documentElement.className = ''
  })

  it('switches between the three workflows and remembers the active view', async () => {
    const wrapper = mountApp()

    expect(wrapper.get('[data-view="registration"]').attributes('aria-current')).toBe('page')
    await wrapper.get('[data-view="checks"]').trigger('click')

    expect(wrapper.get('h1').text()).toBe('Kiểm tra tài khoản')
    expect(localStorage.getItem('gptreg.view')).toBe('checks')
  })

  it('offers professional icon navigation and a runtime theme toggle', async () => {
    const wrapper = mountApp()

    expect(wrapper.findAll('nav svg').length).toBeGreaterThanOrEqual(3)
    await wrapper.get('[data-testid="theme-toggle"]').trigger('click')

    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('does not expose a connection status chip in the top bar', () => {
    const wrapper = mountApp()

    expect(wrapper.find('.connection-chip').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Đã kết nối')
  })

  it('changes the interface language without reloading', async () => {
    const wrapper = mountApp()

    await wrapper.get('[data-testid="locale-select"]').setValue('en')

    expect(wrapper.get('h1').text()).toBe('Registration')
    expect(wrapper.text()).toContain('Account check')
    expect(document.documentElement.lang).toBe('en')
  })
})
