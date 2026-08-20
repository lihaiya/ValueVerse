<template>
  <section class="evidence-panel">
    <div class="panel-head">
      <div>
        <h3>证据链</h3>
        <p>来自原始文档的可追溯片段</p>
      </div>
      <el-button text :loading="loading" @click="loadEvidence">刷新</el-button>
    </div>

    <el-empty v-if="!loading && evidence.length === 0" description="暂无证据片段" />
    <el-skeleton v-else-if="loading" :rows="3" animated />
    <div v-else class="evidence-list">
      <article v-for="item in evidence" :key="item.id" class="evidence-item">
        <div class="item-meta">
          <el-tag size="small" type="info">{{ item.span.span_type }}</el-tag>
          <span>{{ formatLocator(item.span.locator) }}</span>
          <span>confidence {{ item.span.confidence.toFixed(2) }}</span>
        </div>
        <p>{{ item.quote || item.span.text }}</p>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, type EvidenceItem } from '../api'

const props = defineProps<{ nodeId: string }>()
const evidence = ref<EvidenceItem[]>([])
const loading = ref(false)

async function loadEvidence() {
  loading.value = true
  try {
    const response = await api.get<EvidenceItem[]>(`/api/wiki/node/${props.nodeId}/evidence`, { params: { limit: 12 } })
    evidence.value = response.data
  } finally {
    loading.value = false
  }
}

function formatLocator(locator: Record<string, unknown>) {
  const entries = Object.entries(locator)
  if (!entries.length) return '未标注位置'
  return entries.map(([key, value]) => `${key}:${String(value)}`).join(' / ')
}

watch(() => props.nodeId, loadEvidence)
onMounted(loadEvidence)
</script>
