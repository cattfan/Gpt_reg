import { readonly, ref } from 'vue'

import type { StreamEvent } from '../types'

type Scope = 'registration' | 'checks'
type Listener = (event: StreamEvent) => void

const connected = ref(false)
const listeners: Record<Scope, Set<Listener>> = {
  registration: new Set(),
  checks: new Set(),
}
let source: EventSource | null = null

export const sseConnected = readonly(connected)

export function connectSse() {
  if (source || typeof EventSource === 'undefined') return
  source = new EventSource('/api/sse')
  source.onopen = () => { connected.value = true }
  source.onerror = () => { connected.value = false }
  source.onmessage = (message) => {
    let event: StreamEvent
    try { event = JSON.parse(message.data) as StreamEvent } catch { return }
    const scope: Scope = event.scope === 'check' ? 'checks' : 'registration'
    listeners[scope].forEach((listener) => listener(event))
  }
}

export function subscribeSse(scope: Scope, listener: Listener): () => void {
  listeners[scope].add(listener)
  return () => listeners[scope].delete(listener)
}

export function disconnectSse() {
  source?.close()
  source = null
  connected.value = false
}
