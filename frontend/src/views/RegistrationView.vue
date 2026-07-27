<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  Clipboard, Eraser, ListX, Play, RefreshCw, RotateCcw, Square, Terminal, Trash2,
} from '@lucide/vue'

import StatStrip from '../components/StatStrip.vue'
import StatusBadge from '../components/StatusBadge.vue'
import UiPanel from '../components/UiPanel.vue'
import { confirmAction } from '../composables/useConfirm'
import { showToast } from '../composables/useToast'
import { apiJson, apiText, postJson, presentApiError } from '../services/api'
import { subscribeSse } from '../services/sse'
import type { Job, Limits, StreamEvent } from '../types'

const { t } = useI18n()
const jobs = ref<Job[]>([])
const input = ref('')
const source = ref<'outlook' | 'gmail'>('outlook')
const regMode = ref<'browser' | 'http'>('browser')
const fallbackEnabled = ref(false)
const headless = ref(false)
const with2fa = ref(true)
const concurrency = ref(1)
const limits = ref<Limits>({ concurrency_choices: [1, 2, 5, 10], max_browser: 10, max_http: 200, check_concurrency_choices: [1, 5], max_check: 200 })
const selectedJobId = ref<string | null>(null)
const logs = ref<string[]>([])
const exportFormat = ref<'combo' | 'combo_mail' | 'json'>('combo')
const successOutput = ref('')
const loading = ref(false)
let refreshTimer: number | undefined
let pollTimer: number | undefined
let unsubscribe = () => {}

const comboCount = computed(() => lines(input.value).length)
const activeCount = computed(() => jobs.value.filter((job) => job.status === 'queued' || job.status === 'running').length)
const successCount = computed(() => jobs.value.filter((job) => job.status === 'success').length)
const errorJobs = computed(() => jobs.value.filter((job) => job.status === 'error' || job.status === 'cancelled'))
const errorOutput = computed(() => errorJobs.value.map((job) => `${job.email}|${job.error || job.status}`).join('\n'))
const selectedJob = computed(() => jobs.value.find((job) => job.id === selectedJobId.value))
const maxConcurrency = computed(() => (
  regMode.value === 'browser' || fallbackEnabled.value
    ? limits.value.max_browser
    : limits.value.max_http
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
function scheduleRefresh() {
  if (refreshTimer) return
  refreshTimer = window.setTimeout(() => { refreshTimer = undefined; void refreshJobs() }, 300)
}
async function refreshJobs() {
  try {
    jobs.value = await apiJson<Job[]>('/api/jobs')
    if (selectedJobId.value && !jobs.value.some((job) => job.id === selectedJobId.value)) closeSelectedLog()
    if (successCount.value) await refreshExport()
    else successOutput.value = ''
  } catch (error) { showToast(message(error), 'danger') }
}
async function refreshExport() {
  try { successOutput.value = await apiText(`/api/jobs/export?status=success&fmt=${exportFormat.value}`) }
  catch (error) { showToast(message(error), 'danger') }
}
async function startBatch() {
  if (!comboCount.value) return showToast(t('common.noData'), 'danger')
  loading.value = true
  try {
    const result = await postJson<{ job_ids: string[] }>('/api/jobs/start', {
      input: lines(input.value).join('\n'), source: source.value, reg_mode: regMode.value,
      fallback_enabled: fallbackEnabled.value, headless: headless.value,
      with_2fa: with2fa.value, concurrency: concurrency.value,
    })
    showToast(t('toast.started', { count: result.job_ids.length }), result.job_ids.length ? 'success' : 'default')
    await refreshJobs()
  } catch (error) { showToast(message(error), 'danger') }
  finally { loading.value = false }
}
async function stop(jobId?: string) {
  try {
    await postJson('/api/jobs/stop', jobId ? { job_id: jobId } : {})
    showToast(t('toast.stopped'))
    scheduleRefresh()
  } catch (error) { showToast(message(error), 'danger') }
}
async function retryFailed() {
  await retryJobs()
}
async function retryJob(jobId: string) {
  await retryJobs([jobId])
}
async function retryJobs(jobIds?: string[]) {
  try {
    const result = await postJson<{ job_ids: string[] }>('/api/jobs/retry', {
      headless: headless.value, with_2fa: with2fa.value, reg_mode: regMode.value,
      fallback_enabled: fallbackEnabled.value, concurrency: concurrency.value,
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
    selectedJobId.value = null; logs.value = []
    showToast(t('toast.removed', { count: result.removed }), 'success')
    await refreshJobs()
  } catch (error) { showToast(message(error), 'danger') }
}
async function selectJob(job: Job) {
  if (selectedJobId.value === job.id) return closeSelectedLog()
  selectedJobId.value = job.id
  logs.value = []
  try {
    const nextLogs = (await apiJson<{ lines: string[] }>(`/api/jobs/${job.id}/logs`)).lines.slice(-400)
    if (selectedJobId.value === job.id) logs.value = nextLogs
  }
  catch (error) { showToast(message(error), 'danger') }
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
    logs.value = [...logs.value.slice(-3999), event.line]
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
    const [loadedLimits, settings] = await Promise.all([
      apiJson<Limits>('/api/limits'), apiJson<Record<string, string | null>>('/api/settings'),
    ])
    limits.value = loadedLimits
    source.value = settings['reg.source'] === 'gmail' ? 'gmail' : 'outlook'
    if (!concurrencyOptions.value.includes(concurrency.value)) concurrency.value = concurrencyOptions.value[0] || 1
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
        <template #actions><span class="count-chip">{{ t('registration.comboCount', { count: comboCount }) }}</span></template>
        <div class="form-stack">
          <textarea v-model="input" class="mono-input batch-input" spellcheck="false" placeholder="email|password|refresh_token|client_id" />
          <div class="control-row wrap">
            <div class="segmented" role="group" :aria-label="t('registration.source')">
              <button type="button" :class="{ active: source === 'outlook' }" @click="source = 'outlook'">Outlook</button>
              <button type="button" :class="{ active: source === 'gmail' }" @click="source = 'gmail'">Gmail</button>
            </div>
            <div class="segmented" role="group" :aria-label="t('registration.mode')">
              <button type="button" :class="{ active: regMode === 'browser' }" @click="regMode = 'browser'">{{ t('registration.browser') }}</button>
              <button type="button" :class="{ active: regMode === 'http' }" @click="regMode = 'http'">{{ t('registration.http') }}</button>
            </div>
          </div>
          <p v-if="source === 'gmail'" class="inline-warning">{{ t('registration.gmailUnavailable') }}</p>
          <div class="options-row">
            <label class="switch-control"><input v-model="headless" type="checkbox" :disabled="regMode === 'http'"><span /><b>{{ t('registration.headless') }}</b></label>
            <label class="switch-control"><input v-model="with2fa" type="checkbox"><span /><b>{{ t('registration.twofa') }}</b></label>
            <label class="switch-control"><input v-model="fallbackEnabled" data-testid="engine-fallback" type="checkbox"><span /><b>{{ t('registration.engineFallback') }}</b></label>
            <label class="select-field"><span>{{ t('registration.concurrency') }}</span><select v-model.number="concurrency"><option v-for="value in concurrencyOptions" :key="value" :value="value">{{ value }}</option></select></label>
          </div>
          <div class="action-row">
            <button class="btn primary" data-testid="registration-run" type="button" :disabled="loading || source === 'gmail' || activeCount > 0" @click="startBatch"><Play :size="16" />{{ t('registration.run') }}</button>
            <button class="btn danger ghost-danger" type="button" :disabled="!activeCount" @click="stop()"><Square :size="15" />{{ t('registration.stopAll') }}</button>
            <button class="icon-btn" type="button" :title="t('registration.clearInput')" :aria-label="t('registration.clearInput')" @click="input = ''"><Eraser :size="17" /></button>
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
              <span class="job-identity"><strong>{{ job.email }}</strong><small>{{ job.reg_mode || 'browser' }}<template v-if="elapsed(job)"> · {{ elapsed(job) }}</template></small></span>
              <StatusBadge :status="job.status" :label="statusLabel(job.status)" />
            </button>
            <div class="job-actions">
              <button v-if="job.status === 'running' || job.status === 'queued'" class="icon-btn row-action" type="button" :title="t('common.stop')" :aria-label="`${t('common.stop')} ${job.email}`" @click="stop(job.id)"><Square :size="14" /></button>
              <button v-if="job.status === 'error' || job.status === 'cancelled'" :data-testid="`job-retry-${job.id}`" class="icon-btn row-action retry-action" type="button" :title="t('registration.retryOne')" :aria-label="`${t('registration.retryOne')} ${job.email}`" @click="retryJob(job.id)"><RotateCcw :size="14" /></button>
              <button v-if="['success', 'error', 'cancelled'].includes(job.status)" :data-testid="`job-delete-${job.id}`" class="icon-btn row-action delete-action" type="button" :title="t('registration.deleteOne')" :aria-label="`${t('registration.deleteOne')} ${job.email}`" @click="deleteJob(job)"><Trash2 :size="14" /></button>
            </div>
          </div>
        </div>
        <div v-else class="empty-state"><RefreshCw :size="22" /><span>{{ t('registration.noJobs') }}</span></div>
      </UiPanel>

      <UiPanel :title="t('registration.activity')" class="activity-panel" @click.stop>
        <template #actions><span class="panel-context">{{ selectedJob?.email || t('registration.viewAll') }}</span><button v-if="selectedJobId" class="icon-btn" type="button" :title="t('common.close')" @click="closeSelectedLog"><Terminal :size="16" /></button></template>
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
