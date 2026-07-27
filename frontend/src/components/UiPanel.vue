<script setup lang="ts">
defineProps<{ title: string; subtitle?: string; compact?: boolean }>()
</script>

<template>
  <section class="ui-panel" :class="{ compact }">
    <header class="panel-head">
      <div class="panel-heading"><h2>{{ title }}</h2><p v-if="subtitle">{{ subtitle }}</p></div>
      <div v-if="$slots.actions" class="panel-actions"><slot name="actions" /></div>
    </header>
    <div class="panel-body"><slot /></div>
  </section>
</template>
