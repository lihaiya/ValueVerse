<template>
  <el-collapse @change="onCollapseChange">
    <el-collapse-item name="raw">
      <template #title>
        <span class="collapse-title">原文全文查看器</span>
      </template>
      <div v-if="loading" class="muted">加载中...</div>
      <el-alert v-else-if="error" type="error" :title="error" show-icon :closable="false" />
      <template v-else-if="raw">
        <div class="toolbar raw-toolbar">
          <el-tag>{{ raw.filename }}</el-tag>
          <el-tag type="info">{{ raw.mime_type }}</el-tag>
        </div>
        <iframe v-if="raw.kind === 'pdf' && raw.base64" class="pdf-frame" :src="pdfSrc" />
        <pre v-else class="raw-text">{{ raw.text }}</pre>
      </template>
      <span v-else class="muted">展开后读取原始文件。</span>
    </el-collapse-item>
  </el-collapse>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, type RawContent } from '../api'

const props = defineProps<{
  nodeId: string
}>()

const raw = ref<RawContent | null>(null)
const loading = ref(false)
const error = ref('')

const pdfSrc = computed(() => {
  if (!raw.value?.base64) return ''
  return `data:${raw.value.mime_type};base64,${raw.value.base64}`
})

function scrollToAnchor(id: string) {
  const target = document.getElementById(id)
  target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

defineExpose({ scrollToAnchor })

async function onCollapseChange(active: string | string[]) {
  const opened = Array.isArray(active) ? active.includes('raw') : active === 'raw'
  if (!opened || raw.value || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const response = await api.get<RawContent>(`/api/wiki/raw-content/${props.nodeId}`)
    raw.value = response.data
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

function errorMessage(err: unknown) {
  const maybeResponse = err as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = maybeResponse.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  return maybeResponse.message || '原文读取失败'
}
</script>

<style scoped>
.collapse-title {
  font-weight: 700;
}

.raw-toolbar {
  margin-bottom: 12px;
}
</style>
