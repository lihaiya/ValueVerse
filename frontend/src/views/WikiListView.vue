<template>
  <section class="section">
    <div class="section-header">
      <h2>Wiki 条目</h2>
      <div class="toolbar">
        <el-input v-model="query" clearable placeholder="搜索标题" style="width: 260px" @keyup.enter="loadNodes" />
        <el-button :icon="Search" type="primary" @click="loadNodes">搜索</el-button>
      </div>
    </div>
    <el-table :data="nodes" stripe>
      <el-table-column prop="title" label="标题" min-width="280" />
      <el-table-column prop="type" label="类型" width="160" />
      <el-table-column prop="analysis_status" label="状态" width="140" />
      <el-table-column prop="credibility_score" label="可信度" width="120" />
      <el-table-column prop="updated_at" label="更新时间" width="220" />
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <RouterLink :to="`/wiki/${row.id}`">
            <el-button size="small" :icon="View">打开</el-button>
          </RouterLink>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Search, View } from '@element-plus/icons-vue'
import { api, type WikiNodeListItem } from '../api'

const query = ref('')
const nodes = ref<WikiNodeListItem[]>([])
const route = useRoute()

async function loadNodes() {
  const response = await api.get<WikiNodeListItem[]>('/api/wiki/nodes', { params: { q: query.value || undefined, limit: 200 } })
  nodes.value = response.data
}

onMounted(() => {
  query.value = String(route.query.q || '')
  loadNodes()
})
</script>
