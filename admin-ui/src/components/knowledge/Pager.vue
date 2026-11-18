<script setup lang="ts">
import { Button } from '@/components/ui/button'
defineProps<{ page: number; pages: number; total: number; loading?: boolean }>()
defineEmits<{ change: [page: number] }>()
</script>
<template><div class="mt-5 flex flex-wrap items-center justify-between gap-3 text-sm"><span class="text-muted-foreground">共 {{ total }} 条</span><div class="flex items-center gap-2"><Button type="button" variant="outline" size="sm" :disabled="page <= 1 || loading" @click="$emit('change', page - 1)">上一页</Button><span>{{ page }} / {{ pages }}</span><Button type="button" variant="outline" size="sm" :disabled="page >= pages || loading" @click="$emit('change', page + 1)">下一页</Button></div></div></template>
