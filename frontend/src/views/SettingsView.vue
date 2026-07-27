<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { CheckCircle2, CircleOff, Eye, EyeOff, KeyRound, Network, PlugZap, RefreshCw, Save } from '@lucide/vue'

import UiPanel from '../components/UiPanel.vue'
import { showToast } from '../composables/useToast'
import { ApiRequestError, apiJson, postJson, presentApiError, putJson } from '../services/api'
import type { MailSourceStatus, ProxyItem, ProxySettings, RegistrationSource } from '../types'

type IntegrationId = 'smsbower' | 'accstack'

const { t } = useI18n()
const integrationKeys = ref<Record<IntegrationId, string>>({ smsbower: '', accstack: '' })
const hasIntegrationKey = ref<Record<IntegrationId, boolean>>({ smsbower: false, accstack: false })
const showIntegrationKey = ref<Record<IntegrationId, boolean>>({ smsbower: false, accstack: false })
const integrationStatus = ref<Record<IntegrationId, MailSourceStatus | null>>({ smsbower: null, accstack: null })
const integrationError = ref<Record<IntegrationId, string>>({ smsbower: '', accstack: '' })
const loadingStatus = ref<Record<IntegrationId, boolean>>({ smsbower: false, accstack: false })
const savingKey = ref<Record<IntegrationId, boolean>>({ smsbower: false, accstack: false })
const proxyEnabled = ref(false)
const proxyText = ref('')
const proxyItems = ref<ProxyItem[]>([])
const proxyLineError = ref<{ line?: number; message: string } | null>(null)
const savingProxy = ref(false)

const integrations: Array<{ id: IntegrationId; source: RegistrationSource; setting: string }> = [
  { id: 'smsbower', source: 'gmail_smsbower', setting: 'sms.smsbower.api_key' },
  { id: 'accstack', source: 'gmail_accstack', setting: 'accstack.api_key' },
]
const selectedProxyCount = computed(() => proxyItems.value.filter((item) => item.selected).length)

function message(error: unknown) { return presentApiError(error, t) }
function formatMoney(cents: number | undefined, currency = 'USD') {
  if (cents == null) return '-'
  try { return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(cents / 100) }
  catch { return `$${(cents / 100).toFixed(2)}` }
}
function updateProxyText(value: string) {
  const previous = new Map(proxyItems.value.map((item) => [item.value, item.selected]))
  proxyText.value = value
  proxyItems.value = value.split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => ({ value: line, selected: previous.get(line) ?? false }))
  proxyLineError.value = null
}
async function loadSettings() {
  try {
    const [values, proxies] = await Promise.all([
      apiJson<Record<string, string | null>>('/api/settings'),
      apiJson<ProxySettings>('/api/proxies'),
    ])
    integrations.forEach(({ id, setting }) => { hasIntegrationKey.value[id] = Boolean(values[setting]) })
    proxyEnabled.value = proxies.enabled
    proxyItems.value = (proxies.items || []).map((item) => ({ ...item }))
    proxyText.value = proxyItems.value.map((item) => item.value).join('\n')
  } catch (error) { showToast(message(error), 'danger') }
}
async function refreshIntegration(id: IntegrationId, notify = true) {
  const definition = integrations.find((item) => item.id === id)
  if (!definition) return
  loadingStatus.value[id] = true
  integrationError.value[id] = ''
  try {
    const status = await apiJson<MailSourceStatus>(`/api/mail-sources/status?source=${definition.source}`)
    integrationStatus.value[id] = { ...status, products: status.products || [] }
    hasIntegrationKey.value[id] = hasIntegrationKey.value[id] || status.configured
  } catch (error) {
    integrationStatus.value[id] = null
    integrationError.value[id] = message(error)
    if (notify) showToast(integrationError.value[id], 'danger')
  } finally { loadingStatus.value[id] = false }
}
async function saveIntegration(id: IntegrationId) {
  const definition = integrations.find((item) => item.id === id)
  const key = integrationKeys.value[id].trim()
  if (!definition || !key) return
  savingKey.value[id] = true
  try {
    await postJson('/api/settings', { [definition.setting]: key })
    hasIntegrationKey.value[id] = true
    integrationKeys.value[id] = ''
    showToast(t('toast.saved'), 'success')
    await refreshIntegration(id, false)
  } catch (error) { showToast(message(error), 'danger') }
  finally { savingKey.value[id] = false }
}
async function saveProxy() {
  savingProxy.value = true
  proxyLineError.value = null
  try {
    const result = await putJson<ProxySettings>('/api/proxies', {
      enabled: proxyEnabled.value,
      items: proxyItems.value.map(({ value, selected }) => ({ value, selected })),
    })
    proxyEnabled.value = result.enabled
    proxyItems.value = (result.items || []).map((item) => ({ ...item }))
    proxyText.value = proxyItems.value.map((item) => item.value).join('\n')
    showToast(t('toast.saved'), 'success')
  } catch (error) {
    if (error instanceof ApiRequestError && error.detail && typeof error.detail === 'object') {
      const detail = error.detail as { line?: unknown; message?: unknown }
      proxyLineError.value = {
        line: typeof detail.line === 'number' ? detail.line : undefined,
        message: typeof detail.message === 'string' ? detail.message : error.message,
      }
    } else proxyLineError.value = { message: message(error) }
  } finally { savingProxy.value = false }
}

onMounted(async () => {
  await loadSettings()
  await Promise.all(integrations.map(({ id }) => refreshIntegration(id, false)))
})
</script>

<template>
  <div class="workspace settings-workspace" data-testid="settings-view">
    <div class="settings-layout">
      <nav class="settings-nav" :aria-label="t('settings.navigation')">
        <a href="#settings-integrations"><PlugZap :size="16" />{{ t('settings.integrations') }}</a>
        <a href="#settings-proxy"><Network :size="16" />{{ t('settings.proxy') }}</a>
      </nav>

      <div class="settings-content">
        <UiPanel id="settings-integrations" :title="t('settings.integrations')" :subtitle="t('settings.integrationsHint')" class="settings-section-panel">
          <section v-for="integration in integrations" :key="integration.id" class="integration-section">
            <header class="integration-heading">
              <div>
                <component :is="integrationStatus[integration.id]?.configured || hasIntegrationKey[integration.id] ? CheckCircle2 : CircleOff" :size="17" />
                <span><strong>{{ t(`settings.${integration.id}`) }}</strong><small>{{ integrationStatus[integration.id]?.configured || hasIntegrationKey[integration.id] ? t('settings.configured') : t('settings.notConfigured') }}</small></span>
              </div>
              <button class="icon-btn" type="button" :title="t('settings.refreshStatus')" :aria-label="`${t('settings.refreshStatus')} ${t(`settings.${integration.id}`)}`" :disabled="loadingStatus[integration.id]" @click="refreshIntegration(integration.id)"><RefreshCw :size="16" :class="{ spinning: loadingStatus[integration.id] }" /></button>
            </header>
            <div class="integration-body">
              <div class="integration-metrics">
                <div><span>{{ t('settings.balance') }}</span><strong>{{ integrationStatus[integration.id] ? formatMoney(integrationStatus[integration.id]?.balance, integrationStatus[integration.id]?.currency) : '-' }}</strong></div>
                <div><span>{{ t('settings.price') }}</span><strong>{{ integrationStatus[integration.id] ? formatMoney(integrationStatus[integration.id]?.price, integrationStatus[integration.id]?.currency) : '-' }}</strong></div>
                <div><span>{{ t('settings.inventory') }}</span><strong>{{ integrationStatus[integration.id]?.stock ?? '-' }}</strong></div>
                <div><span>{{ t('settings.affordable') }}</span><strong>{{ integrationStatus[integration.id]?.affordable ?? '-' }}</strong></div>
              </div>
              <div class="integration-key-row">
                <label class="field">
                  <span><KeyRound :size="14" />{{ t('settings.apiKey') }}</span>
                  <span class="password-field">
                    <input v-model="integrationKeys[integration.id]" :data-testid="`${integration.id}-api-key`" :type="showIntegrationKey[integration.id] ? 'text' : 'password'" autocomplete="new-password" :placeholder="hasIntegrationKey[integration.id] ? '••••••••••••' : `${t(`settings.${integration.id}`)} API key`">
                    <button type="button" :title="t(showIntegrationKey[integration.id] ? 'common.hide' : 'common.show')" :aria-label="t(showIntegrationKey[integration.id] ? 'common.hide' : 'common.show')" @click="showIntegrationKey[integration.id] = !showIntegrationKey[integration.id]"><EyeOff v-if="showIntegrationKey[integration.id]" :size="16" /><Eye v-else :size="16" /></button>
                  </span>
                </label>
                <button class="btn primary" type="button" :disabled="savingKey[integration.id] || !integrationKeys[integration.id].trim()" @click="saveIntegration(integration.id)"><Save :size="16" />{{ t('settings.saveKey') }}</button>
              </div>
              <p v-if="integrationError[integration.id] || integrationStatus[integration.id]?.reason" class="field-error">{{ integrationError[integration.id] || integrationStatus[integration.id]?.reason }}</p>
            </div>
          </section>
        </UiPanel>

        <UiPanel id="settings-proxy" :title="t('settings.proxy')" :subtitle="t('settings.proxyHint')" class="settings-section-panel proxy-settings-panel">
          <div class="proxy-toolbar">
            <label class="switch-control"><input v-model="proxyEnabled" data-testid="proxy-enabled" type="checkbox"><span /><b>{{ t('settings.useProxy') }}</b></label>
            <span class="proxy-mode" :class="{ active: proxyEnabled }">{{ t(proxyEnabled ? (selectedProxyCount ? 'settings.selectedRandom' : 'settings.proxyAllUsed') : 'settings.directMode') }}</span>
          </div>
          <div class="proxy-editor-grid">
            <label class="field proxy-editor-field">
              <span>{{ t('settings.proxyPool') }}</span>
              <textarea :value="proxyText" data-testid="proxy-editor" class="mono-input settings-textarea" spellcheck="false" placeholder="user:pass@host:port" @input="updateProxyText(($event.target as HTMLTextAreaElement).value)" />
            </label>
            <div class="proxy-preview">
              <header><span>{{ t('settings.proxyCount', { selected: selectedProxyCount, total: proxyItems.length }) }}</span></header>
              <div v-if="proxyItems.length" class="proxy-list">
                <label v-for="(proxy, index) in proxyItems" :key="`${proxy.value}-${index}`" class="proxy-row" :class="{ invalid: proxyLineError?.line === index + 1 }">
                  <input v-model="proxy.selected" :data-testid="`proxy-selected-${index}`" type="checkbox">
                  <span class="proxy-check" aria-hidden="true"><CheckCircle2 :size="14" /></span>
                  <code>{{ proxy.value }}</code>
                  <small>{{ index + 1 }}</small>
                </label>
              </div>
              <div v-else class="empty-state compact proxy-empty">{{ t('settings.proxyEmpty') }}</div>
            </div>
          </div>
          <p v-if="proxyLineError" :data-testid="proxyLineError.line ? `proxy-line-error-${proxyLineError.line}` : 'proxy-error'" class="field-error proxy-error">
            <strong v-if="proxyLineError.line">{{ t('settings.line', { line: proxyLineError.line }) }}:</strong> {{ proxyLineError.message }}
          </p>
          <div class="settings-action-bar">
            <span>{{ t('settings.proxyCount', { selected: selectedProxyCount, total: proxyItems.length }) }}</span>
            <button class="btn primary" data-testid="proxy-save" type="button" :disabled="savingProxy" @click="saveProxy"><Save :size="16" />{{ t('settings.saveProxies') }}</button>
          </div>
        </UiPanel>
      </div>
    </div>
  </div>
</template>
