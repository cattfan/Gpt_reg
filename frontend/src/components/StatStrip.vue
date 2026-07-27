<script setup lang="ts">
defineProps<{ items: Array<{ label: string; value: string | number; tone?: string }> }>()
</script>

<template>
  <div class="stat-strip">
    <div v-for="item in items" :key="item.label" class="stat-item" :class="item.tone && `stat-${item.tone}`">
      <strong>{{ item.value }}</strong><span>{{ item.label }}</span>
    </div>
  </div>
</template>
