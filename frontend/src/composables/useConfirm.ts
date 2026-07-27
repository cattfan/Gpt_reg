import { readonly, ref } from 'vue'

interface ConfirmRequest { title: string; message: string; resolve: (answer: boolean) => void }

const request = ref<ConfirmRequest | null>(null)

export function confirmAction(title: string, message: string): Promise<boolean> {
  return new Promise((resolve) => { request.value = { title, message, resolve } })
}

export function settleConfirm(answer: boolean) {
  request.value?.resolve(answer)
  request.value = null
}

export const confirmRequest = readonly(request)
