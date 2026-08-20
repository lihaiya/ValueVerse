<template>
  <section class="section">
    <div class="section-header">
      <h2>Recall 检索</h2>
      <el-button type="primary" :icon="Search" @click="recall">检索</el-button>
    </div>
    <el-input v-model="query" type="textarea" :rows="3" placeholder="输入价值投资视角的问题" />
    <div v-if="answer" class="answer">
      <h3>回答</h3>
      <p>{{ answer.answer }}</p>
      <el-tag>{{ answer.memory_backend }}</el-tag>
      <el-tag type="success">confidence {{ answer.confidence }}</el-tag>
      <ul>
        <li v-for="citation in answer.citations" :key="citation.link">
          <RouterLink v-if="citation.node_id" :to="`/wiki/${citation.node_id}`">{{ citation.link }}</RouterLink>
          <span v-else>{{ citation.link }}</span>
        </li>
      </ul>
    </div>
  </section>

  <section class="section">
    <div class="section-header">
      <h2>Forget / Improve</h2>
    </div>
    <el-tabs>
      <el-tab-pane label="Forget">
        <el-form label-width="120px">
          <el-form-item label="Doc Hash">
            <el-input v-model="forgetForm.doc_hash" />
          </el-form-item>
          <el-form-item label="Entity URN">
            <el-input v-model="forgetForm.entity_urn" />
          </el-form-item>
          <el-form-item label="Reason">
            <el-input v-model="forgetForm.reason" />
          </el-form-item>
          <el-button :icon="Delete" type="danger" @click="forget">执行遗忘</el-button>
        </el-form>
      </el-tab-pane>
      <el-tab-pane label="Improve">
        <el-form label-width="120px">
          <el-form-item label="Node ID">
            <el-input v-model="improveForm.node_id" />
          </el-form-item>
          <el-form-item label="Field">
            <el-input v-model="improveForm.field" />
          </el-form-item>
          <el-form-item label="Correction">
            <el-input v-model="improveForm.correction" />
          </el-form-item>
          <el-form-item label="Reason">
            <el-input v-model="improveForm.reason" />
          </el-form-item>
          <el-button :icon="EditPen" type="primary" @click="improve">提交修正</el-button>
        </el-form>
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete, EditPen, Search } from '@element-plus/icons-vue'
import { api } from '../api'

const query = ref('')
const answer = ref<any>(null)
const forgetForm = reactive({ doc_hash: '', entity_urn: '', reason: '' })
const improveForm = reactive({ node_id: '', field: 'analysis_status', correction: '', reason: '' })

async function recall() {
  const response = await api.post('/api/memory/recall', { query: query.value, top_k: 5 })
  answer.value = response.data
}

async function forget() {
  await api.post('/api/memory/forget', {
    doc_hash: forgetForm.doc_hash || undefined,
    entity_urn: forgetForm.entity_urn || undefined,
    reason: forgetForm.reason,
  })
  ElMessage.success('遗忘操作已记录')
}

async function improve() {
  await api.post('/api/memory/improve', {
    node_id: improveForm.node_id,
    field: improveForm.field,
    correction: improveForm.correction,
    reason: improveForm.reason,
  })
  ElMessage.success('修正操作已记录')
}
</script>

<style scoped>
.answer {
  margin-top: 14px;
  padding: 14px;
  border: 1px solid #dde7ea;
  border-radius: 8px;
  background: #fbfcfc;
}
</style>

