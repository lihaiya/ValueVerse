<template>
  <RouterLink v-if="resolvedId" class="wikilink" :to="`/wiki/${resolvedId}`" @click="$emit('click')">
    [[{{ title }}]]
  </RouterLink>
  <span v-else class="wikilink missing" @mouseenter="$emit('missing', title)">
    [[{{ title }}]] <small>待创建</small>
  </span>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useWikiStore } from '../stores/wiki'

const props = defineProps<{ title: string }>()
defineEmits<{ click: []; missing: [title: string] }>()

const wikiStore = useWikiStore()
const resolvedId = ref<string | undefined>()

async function resolve() {
  resolvedId.value = await wikiStore.resolveLink(props.title)
}

watch(() => props.title, resolve)
onMounted(resolve)
</script>

<style scoped>
.wikilink {
  color: #1890ff;
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.wikilink:hover {
  border-radius: 4px;
  background: #e6f4ff;
}

.missing {
  color: #b26a00;
  text-decoration: none;
}
</style>

