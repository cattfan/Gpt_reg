import { readonly, ref } from 'vue'

export interface ToastItem { id: number; message: string; tone: 'default' | 'danger' | 'success' }

const items = ref<ToastItem[]>([])
let nextId = 1

export function showToast(message: string, tone: ToastItem['tone'] = 'default') {
  const id = nextId++
  items.value.push({ id, message, tone })
  window.setTimeout(() => dismissToast(id), 4200)
}

export function dismissToast(id: number) {
  items.value = items.value.filter((item) => item.id !== id)
}

export const toasts = readonly(items)
