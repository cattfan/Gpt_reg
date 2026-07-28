import { putJson } from './api'
import type { ProxyItem, ProxySettings } from '../types'

export function normalizeProxyItems(items: ProxyItem[] | undefined): ProxyItem[] {
  return (items || []).map((item) => ({ ...item }))
}

export function selectProxyItem(items: ProxyItem[], index: number, selected: boolean): ProxyItem[] | null {
  const current = items[index]
  if (!current || current.selected === selected) return items
  if (!selected && current.selected && items.filter((item) => item.selected).length === 1) return null
  return items.map((item, itemIndex) => itemIndex === index ? { ...item, selected } : { ...item })
}

export function proxyPayload(items: ProxyItem[]) {
  return {
    items: items.map(({ value, selected }) => ({ value, selected })),
  }
}

export async function persistProxyItems(items: ProxyItem[]): Promise<ProxySettings> {
  return putJson<ProxySettings>('/api/proxies', proxyPayload(items))
}
