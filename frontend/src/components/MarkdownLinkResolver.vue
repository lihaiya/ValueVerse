<template>
  <span class="wiki-link-text">
    <template v-for="(part, index) in parts" :key="`${part.text}-${index}`">
      <RouterLink v-if="part.kind === 'known'" class="wiki-link" :to="`/wiki/${part.id}`">
        [[{{ part.text }}]]
      </RouterLink>
      <span v-else-if="part.kind === 'missing'" class="missing-link" @mouseenter="emitMissing(part.text)">
        [[{{ part.text }}]] <el-tag size="small" type="warning">⚠️ 待创建</el-tag>
      </span>
      <span v-else>{{ part.text }}</span>
    </template>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface Part {
  kind: 'text' | 'known' | 'missing'
  text: string
  id?: string
}

const props = defineProps<{
  text: string
  knownTitles?: Record<string, string>
}>()

const emit = defineEmits<{
  'missing-link': [title: string]
}>()

const parts = computed<Part[]>(() => {
  const output: Part[] = []
  const regex = /\[\[([^\]]+)]]/g
  let last = 0
  let match: RegExpExecArray | null
  while ((match = regex.exec(props.text)) !== null) {
    if (match.index > last) {
      output.push({ kind: 'text', text: props.text.slice(last, match.index) })
    }
    const title = match[1].trim()
    const id = props.knownTitles?.[title]
    output.push(id ? { kind: 'known', text: title, id } : { kind: 'missing', text: title })
    last = match.index + match[0].length
  }
  if (last < props.text.length) {
    output.push({ kind: 'text', text: props.text.slice(last) })
  }
  return output
})

function emitMissing(title: string) {
  emit('missing-link', title)
}
</script>

<style scoped>
.wiki-link {
  color: #0f766e;
  font-weight: 600;
}

.missing-link {
  color: #9a5b13;
}
</style>

