<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Download, Eraser, Play, RotateCcw, Search, Square, Trash2 } from '@lucide/vue'

import StatStrip from '../components/StatStrip.vue'
import StatusBadge from '../components/StatusBadge.vue'
import UiPanel from '../components/UiPanel.vue'
import { confirmAction } from '../composables/useConfirm'
import { showToast } from '../composables/useToast'
import { apiJson, apiText, postJson, presentApiError } from '../services/api'
import { subscribeSse } from '../services/sse'
import type { CheckRecord, Limits, StreamEvent } from '../types'

const { t } = useI18n()
const input = ref('')
const checks = ref<CheckRecord[]>([])
const concurrency = ref(5)
const concurrencyOptions = ref([1, 2, 5, 10, 20, 50, 100, 200])
const search = ref('')
const statusFilter = ref('all')
const planFilter = ref('all')
const loading = ref(false)
let refreshTimer: number | undefined
let pollTimer: number | undefined
let unsubscribe = () => {}

const lineCount = computed(() => lines(input.value).length)
const runningCount = computed(() => checks.value.filter((row) => row.status === 'running' || row.status === 'queued').length)
const liveCount = computed(() => checks.value.filter((row) => row.status === 'live').length)
const invalidCount = computed(() => checks.value.filter((row) => ['die', 'error', 'onboarding', 'cancelled'].includes(row.status)).length)
const plans = computed(() => [...new Set(checks.value.map((row) => row.plan).filter(Boolean) as string[])].sort())
const filteredChecks = computed(() => checks.value.filter((row) => {
  const matchesSearch = row.email.toLowerCase().includes(search.value.trim().toLowerCase())
  const matchesStatus = statusFilter.value === 'all' || row.status === statusFilter.value
  const matchesPlan = planFilter.value === 'all' || (row.plan || '').toLowerCase() === planFilter.value.toLowerCase()
  return matchesSearch && matchesStatus && matchesPlan
}))
const stats = computed(() => [
  { label: t('common.total'), value: checks.value.length },
  { label: t('checks.live'), value: liveCount.value, tone: 'success' },
  { label: t('common.running'), value: runningCount.value, tone: 'running' },
  { label: t('checks.invalid'), value: invalidCount.value, tone: 'error' },
])

function lines(value: string) { return value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean) }
function message(error: unknown) { return presentApiError(error, t) }
function statusLabel(status: string) { return t(`status.${status}`) }
function detail(row: CheckRecord) {
  if (row.status !== 'live') return row.error || ''
  const parts = [row.plan_detail, row.has_subscription ? t('checks.subscription') : '', row.expires_at ? `${t('checks.expires')}: ${row.expires_at}` : '', row.deactivated ? t('checks.deactivated') : '']
  return parts.filter(Boolean).join(' · ')
}
function scheduleRefresh() {
  if (refreshTimer) return
  refreshTimer = window.setTimeout(() => { refreshTimer = undefined; void refreshChecks() }, 300)
}
async function refreshChecks() {
  try { checks.value = await apiJson<CheckRecord[]>('/api/checks') }
  catch (error) { showToast(message(error), 'danger') }
}
async function startChecks() {
  if (!lineCount.value) return showToast(t('common.noData'), 'danger')
  loading.value = true
  try {
    const result = await postJson<{ check_ids: string[] }>('/api/checks/start', { input: lines(input.value).join('\n'), concurrency: concurrency.value })
    showToast(t('toast.started', { count: result.check_ids.length }), result.check_ids.length ? 'success' : 'default')
    scheduleRefresh()
  } catch (error) { showToast(message(error), 'danger') }
  finally { loading.value = false }
}
async function stopChecks() {
  try { await postJson('/api/checks/stop', {}); showToast(t('toast.stopped')); scheduleRefresh() }
  catch (error) { showToast(message(error), 'danger') }
}
async function retryChecks() {
  try {
    const result = await postJson<{ check_ids: string[] }>('/api/checks/retry', { concurrency: concurrency.value })
    showToast(t('toast.retrying', { count: result.check_ids.length }), result.check_ids.length ? 'success' : 'default')
    scheduleRefresh()
  } catch (error) { showToast(message(error), 'danger') }
}
async function exportLive() {
  try {
    const value = await apiText('/api/checks/export?status=live')
    if (!value.trim()) return showToast(t('toast.nothingToCopy'))
    await navigator.clipboard.writeText(value)
    showToast(t('toast.copied', { count: lines(value).length }), 'success')
  } catch (error) { showToast(message(error), 'danger') }
}
async function clearResults() {
  if (!await confirmAction(t('confirm.clearTitle'), t('confirm.clearChecks'))) return
  try {
    const result = await postJson<{ removed: number }>('/api/checks/clear', { scope: 'done' })
    showToast(t('toast.removed', { count: result.removed }), 'success')
    await refreshChecks()
  } catch (error) { showToast(message(error), 'danger') }
}
function handleEvent(event: StreamEvent) { if (event.type === 'check' || event.type === 'batch') scheduleRefresh() }

onMounted(async () => {
  unsubscribe = subscribeSse('checks', handleEvent)
  try {
    const values = await apiJson<Limits>('/api/limits')
    concurrencyOptions.value = values.check_concurrency_choices.filter((value) => value <= values.max_check)
    if (!concurrencyOptions.value.includes(concurrency.value)) concurrency.value = concurrencyOptions.value[0] || 1
  } catch (error) { showToast(message(error), 'danger') }
  await refreshChecks()
  pollTimer = window.setInterval(refreshChecks, 8000)
})
onBeforeUnmount(() => { unsubscribe(); window.clearInterval(pollTimer); window.clearTimeout(refreshTimer) })
</script>

<template>
  <div class="workspace checks-workspace" data-testid="checks-view">
    <StatStrip :items="stats" />
    <div class="checks-layout">
      <UiPanel :title="t('checks.batch')" class="check-input-panel">
        <template #actions><span class="count-chip">{{ t('checks.lineCount', { count: lineCount }) }}</span></template>
        <div class="form-stack">
          <textarea v-model="input" class="mono-input check-input" spellcheck="false" placeholder="mail|pass|2fa&#10;mail|pass|2fa|email|mailpass|refresh|client_id" />
          <div class="options-row">
            <label class="select-field"><span>{{ t('checks.concurrency') }}</span><select v-model.number="concurrency"><option v-for="value in concurrencyOptions" :key="value" :value="value">{{ value }}</option></select></label>
          </div>
          <div class="action-row">
            <button class="btn primary" data-testid="checks-run" type="button" :disabled="loading || runningCount > 0" @click="startChecks"><Play :size="16" />{{ t('checks.run') }}</button>
            <button class="btn danger ghost-danger" type="button" :disabled="!runningCount" @click="stopChecks"><Square :size="15" />{{ t('checks.stop') }}</button>
            <button class="icon-btn" type="button" :title="t('registration.clearInput')" @click="input = ''"><Eraser :size="17" /></button>
          </div>
        </div>
      </UiPanel>

      <UiPanel :title="t('checks.results')" class="check-results-panel">
        <template #actions>
          <button class="icon-btn" type="button" :title="t('checks.retry')" @click="retryChecks"><RotateCcw :size="16" /></button>
          <button class="icon-btn" type="button" :title="t('checks.export')" @click="exportLive"><Download :size="16" /></button>
          <button class="icon-btn" type="button" :title="t('checks.clearDone')" @click="clearResults"><Trash2 :size="16" /></button>
        </template>
        <div class="table-toolbar">
          <label class="search-field"><Search :size="16" /><input v-model="search" type="search" :placeholder="t('checks.searchPlaceholder')"></label>
          <select v-model="statusFilter" :aria-label="t('checks.status')"><option value="all">{{ t('checks.allStatuses') }}</option><option v-for="status in ['live','running','queued','die','onboarding','error','cancelled']" :key="status" :value="status">{{ statusLabel(status) }}</option></select>
          <select v-model="planFilter" :aria-label="t('checks.plan')"><option value="all">{{ t('checks.allPlans') }}</option><option v-for="plan in plans" :key="plan" :value="plan">{{ plan }}</option></select>
          <span class="toolbar-count">{{ filteredChecks.length }} / {{ checks.length }}</span>
        </div>
        <div class="table-wrap">
          <table class="data-table check-table">
            <thead><tr><th>{{ t('checks.email') }}</th><th>{{ t('checks.plan') }}</th><th>{{ t('checks.mfa') }}</th><th>{{ t('checks.status') }}</th><th>{{ t('common.details') }}</th></tr></thead>
            <tbody>
              <tr v-for="row in filteredChecks" :key="row.id">
                <td :data-label="t('checks.email')" class="email-cell">{{ row.email }}</td>
                <td :data-label="t('checks.plan')"><span v-if="row.status === 'live'" class="plan-badge">{{ row.plan || '?' }}<small v-if="row.has_subscription">SUB</small></span><span v-else class="muted">-</span></td>
                <td :data-label="t('checks.mfa')"><span v-if="row.mfa_enabled" class="mfa-mark">2FA</span><span v-else class="muted">-</span></td>
                <td :data-label="t('checks.status')"><StatusBadge :status="row.status" :label="statusLabel(row.status)" /></td>
                <td :data-label="t('common.details')" class="detail-cell">{{ detail(row) || '-' }}</td>
              </tr>
              <tr v-if="!filteredChecks.length"><td colspan="5"><div class="empty-state compact">{{ t('checks.noResults') }}</div></td></tr>
            </tbody>
          </table>
        </div>
      </UiPanel>
    </div>
  </div>
</template>
