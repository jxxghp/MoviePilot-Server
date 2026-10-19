<script setup lang="ts">
import { ref, watch } from 'vue'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
const props = withDefaults(defineProps<{ open: boolean; title: string; description: string; word?: string; busy?: boolean }>(), { word: '删除', busy: false })
const emit = defineEmits<{ 'update:open': [value: boolean]; confirm: [] }>()
const confirmation = ref('')
watch(() => props.open, () => { confirmation.value = '' })
</script>
<template>
  <Dialog :open="open" @update:open="value => { if (!busy) emit('update:open', value) }">
    <DialogContent class="sm:max-w-md">
      <DialogHeader><DialogTitle>{{ title }}</DialogTitle><DialogDescription>{{ description }}</DialogDescription></DialogHeader>
      <form id="confirm-action" class="space-y-2" @submit.prevent="confirmation === word && !busy && emit('confirm')">
        <Label for="confirm-input">请输入“{{ word }}”确认操作</Label>
        <Input id="confirm-input" v-model="confirmation" autocomplete="off" :disabled="busy" />
      </form>
      <DialogFooter><Button variant="outline" :disabled="busy" @click="emit('update:open', false)">取消</Button><Button variant="destructive" type="submit" form="confirm-action" :disabled="confirmation !== word || busy">{{ busy ? '处理中…' : `确认${word}` }}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
</template>
