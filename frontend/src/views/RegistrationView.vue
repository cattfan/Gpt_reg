<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  Clipboard, Eraser, ListX, Minus, Play, Plus, RefreshCw, RotateCcw, Square, Terminal, Trash2,
} from '@lucide/vue'

import StatStrip from '../components/StatStrip.vue'
import StatusBadge from '../components/StatusBadge.vue'
import UiPanel from '../components/UiPanel.vue'
import { confirmAction } from '../composables/useConfirm'
import { showToast } from '../composables/useToast'
import { apiJson, apiText, postJson, presentApiError } from '../services/api'
import { subscribeSse } from '../services/sse'
import type {
  Job, Limits, MailProduct, MailSourceStatus, ProfileRegion, ProxySettings, RegistrationSource, StreamEvent,
} from '../types'

const { t } = useI18n()
const jobs = ref<Job[]>([])
const input = ref('')
const source = ref<RegistrationSource>('outlook')
const profileRegion = ref<ProfileRegion>('vi')
const rentalCount = ref(1)
const productId = ref('')
const mailStatus = ref<MailSourceStatus | null>(null)
const sourceError = ref('')
const sourceLoading = ref(false)
const regMode = ref<'browser' | 'http'>('browser')
const fallbackEnabled = ref(false)
const proxyEnabled = ref(false)
const headless = ref(false)
const with2fa = ref(true)
const concurrency = ref(1)
const limits = ref<Limits>({ concurrency_choices: [1, 2, 5, 10], max_browser: 10, max_http: 200, check_concurrency_choices: [1, 5], max_check: 200 })
const selectedJobId = ref<string | null>(null)
const logs = ref<string[]>([])
const exportFormat = ref<'combo' | 'combo_mail' | 'json'>('combo')
const successOutput = ref('')
const loading = ref(false)
const batchRunning = ref(false)
let sourceRequest = 0
let refreshTimer: number | undefined
let pollTimer: number | undefined
let unsubscribe = () => {}

const isGmail = computed(() => source.value !== 'outlook')
const comboCount = computed(() => lines(input.value).length)
const activeCount = computed(() => jobs.value.filter((job) => job.status === 'queued' || job.status === 'running').length)
const successCount = computed(() => jobs.value.filter((job) => job.status === 'success').length)
const errorJobs = computed(() => jobs.value.filter((job) => job.status === 'error' || job.status === 'cancelled'))
const errorOutput = computed(() => errorJobs.value.map((job) => `${job.email}|${job.error || job.status}`).join('\n'))
const selectedJob = computed(() => jobs.value.find((job) => job.id === selectedJobId.value))
const selectedProduct = computed<MailProduct | null>(() => mailStatus.value?.products.find((product) => product.id === productId.value) || null)
const sourceStock = computed(() => selectedProduct.value?.stock ?? mailStatus.value?.stock ?? 0)
const sourcePrice = computed(() => selectedProduct.value?.price ?? mailStatus.value?.price ?? 0)
const sourceAffordable = computed(() => sourcePrice.value > 0 && mailStatus.value
  ? Math.min(sourceStock.value, Math.floor(mailStatus.value.balance / sourcePrice.value))
  : mailStatus.value?.affordable ?? 0)
const canStart = computed(() => {
  if (loading.value || batchRunning.value || activeCount.value > 0) return false
  if (!isGmail.value) return comboCount.value > 0
  return Boolean(mailStatus.value?.configured)
    && Number.isInteger(rentalCount.value)
    && rentalCount.value > 0
    && rentalCount.value <= sourceStock.value
    && rentalCount.value <= sourceAffordable.value
    && (source.value !== 'gmail_accstack' || Boolean(productId.value))
})
const maxConcurrency = computed(() => (
  regMode.value === 'browser' || fallbackEnabled.value ? limits.value.max_browser : limits.value.max_http
))
const concurrencyOptions = computed(() => limits.value.concurrency_choices.filter((value) => value <= maxConcurrency.value))
const stats = computed(() => [
  { label: t('common.total'), value: jobs.value.length },
  { label: t('common.success'), value: successCount.value, tone: 'success' },
  { label: t('common.running'), value: activeCount.value, tone: 'running' },
  { label: t('common.errors'), value: errorJobs.value.length, tone: 'error' },
])

function lines(value: string) { return value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean) }
function statusLabel(status: string) { return t(`status.${status}`) }
function elapsed(job: Job) {
  const seconds = job.browser_seconds ?? job.http_seconds
  return seconds == null ? '' : `${seconds.toFixed(1)}s`
}
function formatMoney(amount: number | undefined, currency = 'USD', divisor = 100) {
  if (amount == null) return '-'
  const value = amount / divisor
  try { return new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 3 }).format(value) }
  catch { return `$${new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 3 }).format(value)}` }
}
function scheduleRefresh() {
  if (refreshTimer) return
  refreshTimer = window.setTimeout(() => { refreshTimer = undefined; void refreshJobs() }, 300)
}
async function refreshJobs() {
  try {
    const [nextJobs, runtime] = await Promise.all([
      apiJson<Job[]>('/api/jobs'),
      apiJson<{ running: boolean }>('/api/jobs/status'),
    ])
    jobs.value = nextJobs
    batchRunning.value = runtime.running
    if (selectedJobId.value && !jobs.value.some((job) => job.id === selectedJobId.value)) closeSelectedLog()
    if (successCount.value) await refreshExport()
    else successOutput.value = ''
  } catch (error) { showToast(message(error), 'danger') }
}
async function refreshExport() {
  try { successOutput.value = await apiText(`/api/jobs/export?status=success&fmt=${exportFormat.value}`) }
  catch (error) { showToast(message(error), 'danger') }
}
async function selectSource(next: RegistrationSource) {
  source.value = next
  sourceError.value = ''
  mailStatus.value = null
  productId.value = ''
  if (next !== 'outlook') await refreshMailStatus()
}
function updateProxyEnabled(event: Event) {
  proxyEnabled.value = (event.target as HTMLInputElement).checked
  if (source.value === 'outlook') return
  mailStatus.value = null
  void refreshMailStatus()
}
async function refreshMailStatus() {
  if (source.value === 'outlook') return
  const requestedSource = source.value
  const request = ++sourceRequest
  sourceLoading.value = true
  sourceError.value = ''
  try {
    const query = new URLSearchParams({ source: requestedSource, proxy_enabled: String(proxyEnabled.value) })
    const status = await apiJson<MailSourceStatus>(`/api/mail-sources/status?${query}`)
    if (request !== sourceRequest || source.value !== requestedSource) return
    mailStatus.value = { ...status, products: status.products || [] }
    if (status.products?.length) {
      productId.value = status.products.some((product) => product.id === productId.value)
        ? productId.value
        : status.products[0].id
    }
  } catch (error) {
    if (request === sourceRequest) sourceError.value = message(error)
  } finally {
    if (request === sourceRequest) sourceLoading.value = false
  }
}
async function startBatch() {
  if (!canStart.value) return showToast(t(isGmail.value ? 'registration.sourceUnavailable' : 'common.noData'), 'danger')
  loading.value = true
  batchRunning.value = true
  try {
    const payload: Record<string, unknown> = {
      source: source.value,
      profile_region: profileRegion.value,
      reg_mode: regMode.value,
      fallback_enabled: fallbackEnabled.value,
      proxy_enabled: proxyEnabled.value,
      headless: headless.value,
      with_2fa: with2fa.value,
      concurrency: concurrency.value,
    }
    if (source.value === 'outlook') payload.input = lines(input.value).join('\n')
    else payload.rental_count = rentalCount.value
    if (source.value === 'gmail_accstack' && productId.value) payload.product_id = productId.value
    const result = await postJson<{ job_ids?: string[]; rental_ids?: string[]; rental_count?: number }>('/api/jobs/start', payload)
    const count = result.job_ids?.length ?? result.rental_ids?.length ?? result.rental_count ?? 0
    if (!count) batchRunning.value = false
    showToast(t('toast.started', { count }), count ? 'success' : 'default')
    await refreshJobs()
  } catch (error) {
    batchRunning.value = false
    showToast(message(error), 'danger')
  }
  finally { loading.value = false }
}
async function stop(jobId?: string) {
  try {
    await postJson('/api/jobs/stop', jobId ? { job_id: jobId } : {})
    showToast(t('toast.stopped'))
    scheduleRefresh()
  } catch (error) { showToast(message(error), 'danger') }
}
async function retryFailed() { await retryJobs() }
async function retryJob(jobId: string) { await retryJobs([jobId]) }
async function retryJobs(jobIds?: string[]) {
  try {
    const result = await postJson<{ job_ids: string[] }>('/api/jobs/retry', {
      headless: headless.value, with_2fa: with2fa.value, reg_mode: regMode.value,
      fallback_enabled: fallbackEnabled.value, proxy_enabled: proxyEnabled.value, concurrency: concurrency.value,
      ...(jobIds ? { job_ids: jobIds } : {}),
    })
    showToast(t('toast.retrying', { count: result.job_ids.length }), result.job_ids.length ? 'success' : 'default')
    await refreshJobs()
  } catch (error) { showToast(message(error), 'danger') }
}
async function deleteJob(job: Job) {
  const confirmed = await confirmAction(t('confirm.clearTitle'), t('confirm.deleteOne', { email: job.email }))
  if (!confirmed) return
  try {
    const result = await postJson<{ removed: number }>('/api/jobs/clear', { job_ids: [job.id] })
    if (selectedJobId.value === job.id) closeSelectedLog()
    showToast(t('toast.removed', { count: result.removed }), 'success')
    await refreshJobs()
  } catch (error) { showToast(message(error), 'danger') }
}
async function clearJobs(scope: 'done' | 'all') {
  const confirmed = await confirmAction(t('confirm.clearTitle'), t(scope === 'all' ? 'confirm.clearAll' : 'confirm.clearDone'))
  if (!confirmed) return
  try {
    const result = await postJson<{ removed: number }>('/api/jobs/clear', { scope })
    closeSelectedLog()
    showToast(t('toast.removed', { count: result.removed }), 'success')
    await refreshJobs()
  } catch (error) { showToast(message(error), 'danger') }
}
async function selectJob(job: Job) {
  if (selectedJobId.value === job.id) return closeSelectedLog()
  selectedJobId.value = job.id
  logs.value = []
  try {
    const nextLogs = (await apiJson<{ lines: string[] }>(`/api/jobs/${job.id}/logs`)).lines.slice(-500)
    if (selectedJobId.value === job.id) logs.value = nextLogs
  } catch (error) { showToast(message(error), 'danger') }
}
function closeSelectedLog() { selectedJobId.value = null; logs.value = [] }
async function copyText(value: string) {
  if (!value.trim()) return showToast(t('toast.nothingToCopy'))
  try {
    await navigator.clipboard.writeText(value)
    showToast(t('toast.copied', { count: lines(value).length }), 'success')
  } catch (error) { showToast(message(error), 'danger') }
}
function handleEvent(event: StreamEvent) {
  if (event.type === 'log' && event.line && selectedJobId.value === event.job_id) {
    logs.value = [...logs.value.slice(-499), event.line]
  }
  if (event.type === 'batch') {
    if (event.status === 'running') batchRunning.value = true
    if (event.status === 'idle') batchRunning.value = false
  }
  if (event.type === 'job' || event.type === 'batch') scheduleRefresh()
}
function message(error: unknown) { return presentApiError(error, t) }

watch([regMode, fallbackEnabled], () => {
  if (concurrency.value > maxConcurrency.value) concurrency.value = maxConcurrency.value
})
watch(exportFormat, () => { if (successCount.value) void refreshExport() })

onMounted(async () => {
  unsubscribe = subscribeSse('registration', handleEvent)
  try {
    const [loadedLimits, settings, proxies] = await Promise.all([
      apiJson<Limits>('/api/limits'),
      apiJson<Record<string, string | null>>('/api/settings'),
      apiJson<ProxySettings>('/api/proxies'),
    ])
    limits.value = loadedLimits
    proxyEnabled.value = proxies.enabled
    const savedSource = settings['reg.source'] as RegistrationSource
    source.value = ['outlook', 'gmail_smsbower', 'gmail_accstack'].includes(savedSource) ? savedSource : 'outlook'
    if (!concurrencyOptions.value.includes(concurrency.value)) concurrency.value = concurrencyOptions.value[0] || 1
    if (source.value !== 'outlook') await refreshMailStatus()
  } catch (error) { showToast(message(error), 'danger') }
  await refreshJobs()
  pollTimer = window.setInterval(refreshJobs, 8000)
})
onBeforeUnmount(() => {
  unsubscribe()
  window.clearInterval(pollTimer)
  window.clearTimeout(refreshTimer)
})
</script>

<template>
  <div class="workspace registration-workspace" data-testid="registration-view" @click="closeSelectedLog">
    <StatStrip :items="stats" />

    <div class="registration-grid">
      <UiPanel :title="t('registration.batch')" class="batch-panel">
        <template #actions>
          <span class="count-chip">{{ isGmail ? t('registration.mailboxCount', { count: rentalCount }) : t('registration.comboCount', { count: comboCount }) }}</span>
        </template>
        <div class="form-stack">
          <div class="source-segment segmented" role="group" :aria-label="t('registration.source')">
            <button data-testid="source-outlook" type="button" :class="{ active: source === 'outlook' }" @click="selectSource('outlook')">{{ t('registration.sourceOutlook') }}</button>
            <button data-testid="source-gmail_smsbower" type="button" :class="{ active: source === 'gmail_smsbower' }" @click="selectSource('gmail_smsbower')">Gmail (SMSBower)</button>
            <button data-testid="source-gmail_accstack" type="button" :class="{ active: source === 'gmail_accstack' }" @click="selectSource('gmail_accstack')">Gmail (AccStack)</button>
          </div>

          <textarea
            v-if="source === 'outlook'" v-model="input" data-testid="outlook-input"
            class="mono-input batch-input" spellcheck="false" placeholder="email|password|refresh_token|client_id"
          />
          <div v-else class="rental-workspace">
            <div class="rental-header">
              <label class="field quantity-field">
                <span>{{ t('registration.rentalCount') }}</span>
                <span class="quantity-stepper">
                  <button type="button" :title="t('registration.decrease')" :aria-label="t('registration.decrease')" @click="rentalCount = Math.max(1, rentalCount - 1)"><Minus :size="15" /></button>
                  <input v-model.number="rentalCount" data-testid="rental-count" type="number" min="1" :max="Math.max(1, sourceStock)" inputmode="numeric">
                  <button type="button" :title="t('registration.increase')" :aria-label="t('registration.increase')" @click="rentalCount += 1"><Plus :size="15" /></button>
                </span>
              </label>
              <label v-if="source === 'gmail_accstack' && (mailStatus?.products.length || 0) > 1" class="field product-field">
                <span>{{ t('registration.product') }}</span>
                <select v-model="productId" data-testid="accstack-product">
                  <option v-for="product in mailStatus?.products || []" :key="product.id" :value="product.id">{{ product.name }}</option>
                </select>
              </label>
              <button class="icon-btn source-refresh" type="button" :title="t('common.refresh')" :aria-label="t('common.refresh')" :disabled="sourceLoading" @click="refreshMailStatus"><RefreshCw :size="16" :class="{ spinning: sourceLoading }" /></button>
            </div>
            <div class="source-status-strip" :class="{ unavailable: !mailStatus?.configured || sourceError }">
              <div><span>{{ t('settings.balance') }}</span><strong>{{ mailStatus ? formatMoney(mailStatus.balance, mailStatus.currency, mailStatus.currency_divisor) : '-' }}</strong></div>
              <div><span>{{ t('registration.price') }}</span><strong>{{ mailStatus ? formatMoney(sourcePrice, mailStatus.currency, mailStatus.currency_divisor) : '-' }}</strong></div>
              <div><span>{{ t('settings.inventory') }}</span><strong>{{ mailStatus?.configured ? sourceStock : '-' }}</strong></div>
              <div><span>{{ t('settings.affordable') }}</span><strong>{{ mailStatus?.configured ? sourceAffordable : '-' }}</strong></div>
            </div>
            <p v-if="sourceError || mailStatus?.reason" class="inline-warning">{{ sourceError || mailStatus?.reason }}</p>
          </div>

          <div class="control-row wrap control-groups">
            <div class="segmented" role="group" :aria-label="t('registration.mode')">
              <button type="button" :class="{ active: regMode === 'browser' }" @click="regMode = 'browser'">{{ t('registration.browser') }}</button>
              <button type="button" :class="{ active: regMode === 'http' }" @click="regMode = 'http'">{{ t('registration.http') }}</button>
            </div>
            <div class="segmented profile-segment" role="group" :aria-label="t('registration.profileRegion')">
              <button data-testid="profile-vi" type="button" :class="{ active: profileRegion === 'vi' }" @click="profileRegion = 'vi'">{{ t('registration.profileVi') }}</button>
              <button data-testid="profile-ko" type="button" :class="{ active: profileRegion === 'ko' }" @click="profileRegion = 'ko'">{{ t('registration.profileKo') }}</button>
              <button data-testid="profile-in" type="button" :class="{ active: profileRegion === 'in' }" @click="profileRegion = 'in'">{{ t('registration.profileIn') }}</button>
            </div>
          </div>
          <div class="options-row">
            <label class="switch-control"><input v-model="headless" type="checkbox" :disabled="regMode === 'http'"><span /><b>{{ t('registration.headless') }}</b></label>
            <label class="switch-control"><input v-model="with2fa" type="checkbox"><span /><b>{{ t('registration.twofa') }}</b></label>
            <label class="switch-control"><input v-model="fallbackEnabled" data-testid="engine-fallback" type="checkbox"><span /><b>{{ t('registration.engineFallback') }}</b></label>
            <label class="switch-control"><input :checked="proxyEnabled" data-testid="registration-proxy-enabled" type="checkbox" @change="updateProxyEnabled"><span /><b>{{ t('settings.useProxy') }}</b></label>
            <label class="select-field"><span>{{ t('registration.concurrency') }}</span><select v-model.number="concurrency"><option v-for="value in concurrencyOptions" :key="value" :value="value">{{ value }}</option></select></label>
          </div>
          <div class="action-row">
            <button class="btn primary" data-testid="registration-run" type="button" :disabled="!canStart" @click="startBatch"><Play :size="16" />{{ isGmail ? t('registration.rentAndRun', { count: rentalCount }) : t('registration.run') }}</button>
            <button class="btn danger ghost-danger" type="button" :disabled="!activeCount && !batchRunning" @click="stop()"><Square :size="15" />{{ t('registration.stopAll') }}</button>
            <button v-if="source === 'outlook'" class="icon-btn" type="button" :title="t('registration.clearInput')" :aria-label="t('registration.clearInput')" @click="input = ''"><Eraser :size="17" /></button>
          </div>
        </div>
      </UiPanel>

      <UiPanel :title="t('registration.jobs')" class="jobs-panel">
        <template #actions>
          <button class="icon-btn" type="button" :title="t('registration.retryFailed')" :aria-label="t('registration.retryFailed')" @click="retryFailed"><RotateCcw :size="16" /></button>
          <button class="icon-btn" type="button" :title="t('registration.clearDone')" :aria-label="t('registration.clearDone')" @click="clearJobs('done')"><Trash2 :size="16" /></button>
          <button class="icon-btn" type="button" :title="t('registration.clearAll')" :aria-label="t('registration.clearAll')" @click="clearJobs('all')"><ListX :size="16" /></button>
        </template>
        <div v-if="jobs.length" class="job-list">
          <div v-for="job in jobs" :key="job.id" class="job-row" :class="{ selected: selectedJobId === job.id }">
            <button :data-testid="`job-select-${job.id}`" type="button" class="job-main" @click.stop="selectJob(job)">
              <span class="job-identity"><strong>{{ job.email }}</strong><small>{{ job.reg_mode || 'browser' }}<template v-if="job.profile_region"> · {{ job.profile_region.toUpperCase() }}</template><template v-if="job.alias_index"> · #{{ job.alias_index }}</template><template v-if="elapsed(job)"> · {{ elapsed(job) }}</template></small></span>
              <StatusBadge :status="job.status" :label="statusLabel(job.status)" />
            </button>
            <div class="job-actions">
              <button v-if="job.status === 'running' || job.status === 'queued'" class="icon-btn row-action" type="button" :title="t('common.stop')" :aria-label="`${t('common.stop')} ${job.email}`" @click="stop(job.id)"><Square :size="14" /></button>
              <button v-if="job.status === 'error' || job.status === 'cancelled'" :data-testid="`job-retry-${job.id}`" class="icon-btn row-action retry-action" type="button" :title="t('registration.retryOne')" :aria-label="`${t('registration.retryOne')} ${job.email}`" @click="retryJob(job.id)"><RotateCcw :size="14" /></button>
              <button v-if="['success', 'error', 'cancelled'].includes(job.status)" :data-testid="`job-delete-${job.id}`" class="icon-btn row-action delete-action" type="button" :title="t('registration.deleteOne')" :aria-label="`${t('registration.deleteOne')} ${job.email}`" @click="deleteJob(job)"><Trash2 :size="14" /></button>
            </div>
          </div>
        </div>
        <div v-else class="empty-state jobs-empty"><RefreshCw :size="22" /><span>{{ t('registration.noJobs') }}</span></div>
      </UiPanel>

      <UiPanel :title="t('registration.activity')" class="activity-panel" @click.stop>
        <template #actions>
          <span class="panel-context">{{ selectedJob?.email || t('registration.viewAll') }}</span>
          <button v-if="selectedJobId" class="icon-btn" data-testid="registration-copy-log" type="button" :title="t('registration.copyLog')" :aria-label="t('registration.copyLog')" @click.stop="copyText(logs.join('\n'))"><Clipboard :size="16" /></button>
        </template>
        <pre v-if="logs.length" class="log-view">{{ logs.join('\n') }}</pre>
        <div v-else class="empty-state compact"><Terminal :size="20" /><span>{{ t('registration.noActivity') }}</span></div>
      </UiPanel>

      <UiPanel :title="t('registration.results')" class="results-panel">
        <template #actions>
          <div class="segmented small" role="group" :aria-label="t('registration.exportFormat')">
            <button type="button" :class="{ active: exportFormat === 'combo' }" @click="exportFormat = 'combo'">Combo</button>
            <button type="button" :class="{ active: exportFormat === 'combo_mail' }" @click="exportFormat = 'combo_mail'">+Mail</button>
            <button type="button" :class="{ active: exportFormat === 'json' }" @click="exportFormat = 'json'">JSON</button>
          </div>
        </template>
        <div class="result-columns">
          <div class="result-block"><div><strong>{{ t('registration.successful') }}</strong><span class="count-chip">{{ successCount }}</span><button class="icon-btn" type="button" :title="t('common.copy')" @click="copyText(successOutput)"><Clipboard :size="15" /></button></div><textarea class="result-output" readonly :value="successOutput" /></div>
          <div class="result-block"><div><strong>{{ t('registration.failed') }}</strong><span class="count-chip">{{ errorJobs.length }}</span><button class="icon-btn" type="button" :title="t('common.copy')" @click="copyText(errorOutput)"><Clipboard :size="15" /></button></div><textarea class="result-output" readonly :value="errorOutput" /></div>
        </div>
      </UiPanel>
    </div>
  </div>
</template>
