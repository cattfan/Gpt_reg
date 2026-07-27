<script setup lang="ts">
import { AlertTriangle, X } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import { confirmRequest, settleConfirm } from '../composables/useConfirm'
const { t } = useI18n()
</script>

<template>
  <div v-if="confirmRequest" class="dialog-backdrop" role="presentation" @click.self="settleConfirm(false)">
    <div class="confirm-dialog" role="alertdialog" aria-modal="true" :aria-labelledby="'confirm-title'">
      <div class="dialog-icon"><AlertTriangle :size="20" /></div>
      <div class="dialog-copy"><h2 id="confirm-title">{{ confirmRequest.title }}</h2><p>{{ confirmRequest.message }}</p></div>
      <button class="icon-btn dialog-close" type="button" :aria-label="t('common.cancel')" @click="settleConfirm(false)"><X :size="17" /></button>
      <div class="dialog-actions"><button class="btn secondary" type="button" @click="settleConfirm(false)">{{ t('common.cancel') }}</button><button class="btn danger" type="button" @click="settleConfirm(true)">{{ t('common.confirm') }}</button></div>
    </div>
  </div>
</template>
