<template>
  <div class="meta-badges">
    <el-tag effect="plain" type="primary">{{ meta.type || 'general-doc' }}</el-tag>
    <RouterLink v-if="meta.ticker" :to="`/company/${meta.ticker}`">
      <el-tag effect="dark">{{ meta.ticker }}</el-tag>
    </RouterLink>
    <el-tag :type="statusType">{{ meta.analysis_status || 'unknown' }}</el-tag>
    <el-tag type="success">可信度 {{ score }}</el-tag>
    <el-tag v-if="meta.llm_extraction?.status" :type="meta.llm_extraction.status === 'completed' ? 'success' : 'warning'">
      LLM {{ meta.llm_extraction.status }}
    </el-tag>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  meta: Record<string, any>
}>()

const score = computed(() => Number(props.meta.credibility_score || 0).toFixed(2))
const statusType = computed(() => {
  const status = String(props.meta.analysis_status || '')
  if (status.includes('deprecated')) return 'danger'
  if (status.includes('fallback') || status.includes('disabled')) return 'warning'
  return 'info'
})
</script>
