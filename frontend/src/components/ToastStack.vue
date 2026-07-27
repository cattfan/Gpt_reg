<script setup lang="ts">
import { AlertCircle, CheckCircle2, Info, X } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import { dismissToast, toasts } from '../composables/useToast'
const { t } = useI18n()
</script>

<template>
  <div class="toast-stack" aria-live="polite">
    <div v-for="toast in toasts" :key="toast.id" class="toast" :class="`toast-${toast.tone}`">
      <CheckCircle2 v-if="toast.tone === 'success'" :size="17" /><AlertCircle v-else-if="toast.tone === 'danger'" :size="17" /><Info v-else :size="17" />
      <span>{{ toast.message }}</span>
      <button type="button" :aria-label="t('common.close')" @click="dismissToast(toast.id)"><X :size="15" /></button>
    </div>
  </div>
</template>
