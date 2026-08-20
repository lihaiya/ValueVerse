<template>
  <section class="raw-doc">
    <div class="section-header compact">
      <h2>原文查看器</h2>
      <el-segmented v-model="mode" :options="['底部面板', '右侧分栏']" />
    </div>
    <RawContentViewer ref="innerRef" :node-id="nodeId" />
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import RawContentViewer from './RawContentViewer.vue'

defineProps<{ nodeId: string }>()
const mode = ref('底部面板')
const innerRef = ref<InstanceType<typeof RawContentViewer> | null>(null)

function scrollToAnchor(id: string) {
  innerRef.value?.scrollToAnchor?.(id)
}

defineExpose({ scrollToAnchor })
</script>
