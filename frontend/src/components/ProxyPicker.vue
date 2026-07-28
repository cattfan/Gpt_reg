<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { ProxyItem } from '../types'

const props = withDefaults(defineProps<{
  items: ProxyItem[]
  testIdPrefix: string
  disabled?: boolean
  compact?: boolean
}>(), {
  disabled: false,
  compact: false,
})
const emit = defineEmits<{ toggle: [index: number, selected: boolean] }>()
const { t } = useI18n()
const selectedCount = computed(() => props.items.filter((item) => item.selected).length)

function toggle(index: number, event: Event) {
  emit('toggle', index, (event.target as HTMLInputElement).checked)
}
</script>

<template>
  <div class="proxy-picker runtime-proxy-picker" :class="{ compact }">
    <header>
      <span>{{ t('settings.proxyPool') }}</span>
      <small>{{ t('settings.proxyCount', { selected: selectedCount, total: items.length }) }}</small>
    </header>
    <div v-if="items.length" class="proxy-list">
      <label
        v-for="(proxy, index) in items"
        :key="proxy.value"
        class="proxy-row"
        :class="{ locked: proxy.selected && selectedCount === 1 }"
      >
        <input
          :checked="proxy.selected"
          :data-testid="`${testIdPrefix}-${index}`"
          type="checkbox"
          :aria-label="`${t('settings.useProxy')}: ${proxy.value}`"
          :disabled="disabled || (proxy.selected && selectedCount === 1)"
          @change="toggle(index, $event)"
        >
        <span class="proxy-switch" aria-hidden="true" />
        <code>{{ proxy.value }}</code>
        <small>{{ index + 1 }}</small>
      </label>
    </div>
    <div v-else class="empty-state compact proxy-empty">{{ t('settings.proxyEmpty') }}</div>
  </div>
</template>
