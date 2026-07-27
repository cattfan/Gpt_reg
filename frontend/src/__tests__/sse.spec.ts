import { afterEach, describe, expect, it, vi } from 'vitest'

import { connectSse, disconnectSse, subscribeSse } from '../services/sse'

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  close = vi.fn()

  constructor(public url: string) { FakeEventSource.instances.push(this) }
}

describe('shared SSE connection', () => {
  afterEach(() => {
    disconnectSse()
    FakeEventSource.instances = []
    vi.unstubAllGlobals()
  })

  it('opens one stream and routes registration/check events by scope', () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const registration = vi.fn()
    const checks = vi.fn()
    const offRegistration = subscribeSse('registration', registration)
    const offChecks = subscribeSse('checks', checks)

    connectSse()
    connectSse()
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].url).toBe('/api/sse')

    const source = FakeEventSource.instances[0]
    source.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ type: 'job' }) }))
    source.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ type: 'check', scope: 'check' }) }))

    expect(registration).toHaveBeenCalledTimes(1)
    expect(checks).toHaveBeenCalledTimes(1)
    offRegistration()
    offChecks()
  })
})
