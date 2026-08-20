<template>
  <div class="memory-toolbar">
    <div>
      <strong>记忆操作</strong>
      <span class="muted">当前页作用域</span>
    </div>
    <div class="toolbar">
      <el-button :icon="Delete" type="danger" plain @click="forget">删除词条</el-button>
      <el-button :icon="MagicStick" type="primary" plain @click="improveOpen = true">改进提示</el-button>
      <el-button :icon="Search" type="warning" plain :loading="webEnriching" @click="webEnrich">联网更新</el-button>
      <el-button :icon="TrendCharts" type="success" plain @click="rescore">重新打分</el-button>
    </div>

    <el-dialog v-model="improveOpen" title="提交修正反馈" width="520px">
      <el-form label-width="90px">
        <el-form-item label="字段">
          <el-input v-model="improveForm.field" />
        </el-form-item>
        <el-form-item label="修正">
          <el-input v-model="improveForm.correction" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="原因">
          <el-input v-model="improveForm.reason" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="improveOpen = false">取消</el-button>
        <el-button type="primary" @click="improve">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, MagicStick, Search, TrendCharts } from '@element-plus/icons-vue'
import { api, type WikiNode } from '../api'

const props = defineProps<{ node: WikiNode }>()
const emit = defineEmits<{ updated: []; deleted: [] }>()

const improveOpen = ref(false)
const webEnriching = ref(false)
const improveForm = reactive({ field: 'analysis_status', correction: '', reason: '' })

async function forget() {
  await ElMessageBox.confirm('确认删除当前 Wiki 词条？这会从本地搜索、图谱和关联边中移除。', '删除词条', { type: 'warning' })
  await api.post('/api/memory/forget', {
    node_id: props.node.id,
    doc_hash: props.node.cognee_doc_hash,
    delete_node: true,
    reason: 'frontend delete node',
  })
  ElMessage.success('词条已删除')
  emit('deleted')
}

async function improve() {
  await api.post('/api/memory/improve', {
    node_id: props.node.id,
    field: improveForm.field,
    correction: improveForm.correction,
    reason: improveForm.reason,
  })
  improveOpen.value = false
  ElMessage.success('修正已记录')
  emit('updated')
}

async function webEnrich() {
  webEnriching.value = true
  try {
    await api.post<WikiNode>(`/api/wiki/node/${props.node.id}/web-enrich`, { top_k: 5 }, { timeout: 180000 })
    ElMessage.success('联网补充已写入词条')
    emit('updated')
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    webEnriching.value = false
  }
}

async function rescore() {
  const response = await api.post('/api/scoring/evaluate', { node_id: props.node.id })
  ElMessage.success(`重新打分完成：${response.data.score} / ${response.data.grade}`)
}

function errorMessage(err: unknown) {
  const maybeResponse = err as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = maybeResponse.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  return maybeResponse.message || '联网更新失败'
}
</script>
