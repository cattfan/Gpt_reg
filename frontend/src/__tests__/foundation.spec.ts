// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { URL as NodeUrl } from 'node:url'

import App from '../App.vue'
import { createAppI18n } from '../i18n'

describe('frontend foundation', () => {
  it('mounts the application in a main landmark', () => {
    const wrapper = mount(App, { global: { plugins: [createAppI18n('vi')] } })

    expect(wrapper.get('main').text()).toContain('Gpt_reg')
  })

  it('does not embed a web credential in the HTML shell', () => {
    const html = readFileSync(new NodeUrl('../../index.html', import.meta.url), 'utf8')
    const document = new DOMParser().parseFromString(html, 'text/html')
    const tokenMeta = document.querySelector('meta[name="auth-token"]')

    expect(tokenMeta).toBeNull()
  })
})
