export interface ApiErrorDetail {
  line?: number
  message?: string
  [key: string]: unknown
}

export class ApiRequestError extends Error {
  constructor(public status: number, public detail: unknown, message: string) {
    super(message)
    this.name = 'ApiRequestError'
  }
}

async function responseError(response: Response): Promise<ApiRequestError> {
  let detail: unknown
  try {
    const payload = await response.json() as { detail?: unknown; error?: unknown }
    detail = payload.detail ?? payload.error
  } catch { /* response is not JSON */ }
  const message = typeof detail === 'string' && detail.trim()
    ? detail
    : detail && typeof detail === 'object' && typeof (detail as ApiErrorDetail).message === 'string'
      ? String((detail as ApiErrorDetail).message)
      : detail
        ? JSON.stringify(detail)
        : `HTTP ${response.status}`
  return new ApiRequestError(response.status, detail, message)
}

export async function apiRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(path, { ...init, headers })
  if (!response.ok) throw await responseError(response)
  return response
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  return (await apiRequest(path, init)).json() as Promise<T>
}

export async function apiText(path: string): Promise<string> {
  return (await apiRequest(path)).text()
}

export function postJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
  return apiJson<T>(path, { method: 'POST', body: JSON.stringify(body) })
}

export function putJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
  return apiJson<T>(path, { method: 'PUT', body: JSON.stringify(body) })
}

export function presentApiError(error: unknown, translate: (key: string) => string): string {
  const detail = error instanceof Error ? error.message.trim() : ''
  if (detail === 'Chưa có combo nào.') return translate('errors.noCombos')
  if (detail.startsWith('Nguồn Gmail chưa hỗ trợ')) return translate('errors.gmailUnavailable')
  if (detail === 'job not found') return translate('errors.jobNotFound')
  if (detail.startsWith('Combo sai')) {
    const technical = detail.includes('—') ? detail.slice(detail.indexOf('—')) : ''
    return `${translate('errors.invalidCombo')}${technical ? ` ${technical}` : ''}`
  }
  return detail ? `${translate('toast.requestFailed')}: ${detail}` : translate('toast.requestFailed')
}
