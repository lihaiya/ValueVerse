<template>
  <div class="streaming-response">
    <MarkdownRenderer :content="visibleText" :link-map="linkMap" />
    <span v-if="streaming" class="cursor">▋</span>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import MarkdownRenderer from './MarkdownRenderer.vue'

const props = defineProps<{
  content: string
  streaming?: boolean
  linkMap?: Record<string, string>
}>()

const count = ref(0)
const visibleText = computed(() => props.streaming ? props.content.slice(0, count.value) : props.content)

function animate() {
  count.value = Math.min(props.content.length, count.value + 6)
  if (count.value < props.content.length) window.setTimeout(animate, 16)
}

watch(() => props.content, () => {
  count.value = props.streaming ? 0 : props.content.length
  if (props.streaming) animate()
})

onMounted(() => {
  count.value = props.streaming ? 0 : props.content.length
  if (props.streaming) animate()
})
</script>

