export type JobStatus = 'queued' | 'running' | 'success' | 'error' | 'cancelled'

export interface Job {
  id: string
  email: string
  mail_mode?: string
  reg_mode?: string
  status: JobStatus
  error?: string | null
  password?: string | null
  mfa_activated?: boolean | number
  browser_seconds?: number | null
  http_seconds?: number | null
  mfa_seconds?: number | null
  created_at?: number
  started_at?: number | null
  finished_at?: number | null
  profile_region?: ProfileRegion
  alias_index?: number | null
}

export type RegistrationSource = 'outlook' | 'gmail_smsbower' | 'gmail_accstack'
export type ProfileRegion = 'vi' | 'ko' | 'in'

export interface MailProduct {
  id: string
  name: string
  price: number
  stock: number
  description?: string
}

export interface MailSourceStatus {
  configured: boolean
  balance: number
  currency: string
  currency_divisor?: number
  price: number
  stock: number
  affordable: number
  products: MailProduct[]
  reason?: string
}

export interface ProxyItem {
  id?: number
  value: string
  selected: boolean
}

export interface ProxySettings {
  enabled: boolean
  items: ProxyItem[]
  selected: number
  total: number
}

export type CheckStatus = 'queued' | 'running' | 'live' | 'die' | 'onboarding' | 'error' | 'cancelled'

export interface CheckRecord {
  id: string
  email: string
  status: CheckStatus
  plan?: string | null
  plan_detail?: string | null
  has_subscription: boolean
  expires_at?: string | null
  mfa_enabled: boolean
  deactivated: boolean
  error?: string | null
  seconds?: number | null
}

export interface Limits {
  concurrency_choices: number[]
  max_browser: number
  max_http: number
  check_concurrency_choices: number[]
  max_check: number
}

export interface SmsCountry {
  id: string
  name: string
  cost: number
  count: number
  affordable: number
}

export interface SmsStatus {
  configured: boolean
  ok?: boolean
  reason?: string
  error?: string
  balance?: number
  total_available?: number
  affordable?: number
  countries?: SmsCountry[]
}

export interface StreamEvent {
  type: string
  scope?: string
  job_id?: string
  check_id?: string
  status?: string
  line?: string
  [key: string]: unknown
}
