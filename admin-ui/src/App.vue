<script setup lang="ts">
import { notice, noticeError } from '@/api'
</script>
<template>
  <RouterView />
  <div v-if="notice" role="status" aria-live="polite" class="fixed bottom-5 left-1/2 z-[100] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 rounded-lg border bg-background px-5 py-4 text-sm shadow-lg" :class="noticeError ? 'border-destructive text-destructive' : ''">{{ notice }}</div>
</template>
