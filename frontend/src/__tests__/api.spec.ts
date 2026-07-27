import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiRequestError, apiJson, presentApiError, putJson } from '../services/api'

describe('api client', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends local API requests without an auth header', async () => {
    document.head.innerHTML = ''
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"ok":true}', {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(apiJson('/api/settings')).resolves.toEqual({ ok: true })
    expect(fetchMock.mock.calls[0][1].headers.has('Authorization')).toBe(false)
  })

  it('surfaces FastAPI detail instead of hiding the server error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"detail":"Combo sai"}', {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })))

    await expect(apiJson('/api/jobs/start', { method: 'POST' })).rejects.toThrow('Combo sai')
  })

  it('maps known Vietnamese API errors to the active interface locale', () => {
    const labels: Record<string, string> = { 'errors.invalidCombo': 'Invalid combo', 'toast.requestFailed': 'Request failed' }
    const translate = (key: string) => labels[key] || key

    expect(presentApiError(new Error('Combo sai — dòng 2'), translate)).toBe('Invalid combo — dòng 2')
  })

  it('preserves structured line errors and supports JSON PUT requests', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response('{"detail":{"line":2,"message":"bad proxy"}}', {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })).mockResolvedValueOnce(new Response('{"ok":true}', {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    const rejected = apiJson('/api/proxies')
    await expect(rejected).rejects.toBeInstanceOf(ApiRequestError)
    await expect(putJson('/api/proxies', { enabled: false, items: [] })).resolves.toEqual({ ok: true })
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'PUT' })
  })
})
