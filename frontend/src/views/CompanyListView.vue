<template>
  <section class="section company-directory">
    <div class="section-header">
      <div>
        <p class="eyebrow">Company Panorama</p>
        <h2>公司全景</h2>
        <p class="muted">按已入库公司聚合年报、业务、风险和高管词条</p>
      </div>
      <div class="toolbar">
        <el-input v-model="query" clearable placeholder="搜索公司或证券代码" style="width: 260px" />
        <el-button :icon="Refresh" :loading="loading" @click="loadCompanies">刷新</el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="filteredCompanies" stripe>
      <el-table-column prop="name" label="公司" min-width="220" />
      <el-table-column prop="ticker" label="证券代码" width="130" />
      <el-table-column prop="reportCount" label="年报" width="90" />
      <el-table-column prop="conceptCount" label="概念" width="90" />
      <el-table-column prop="riskCount" label="风险" width="90" />
      <el-table-column prop="peopleCount" label="高管" width="90" />
      <el-table-column label="操作" width="130">
        <template #default="{ row }">
          <RouterLink :to="`/company/${row.routeTicker}`">
            <el-button size="small" :icon="View">打开全景</el-button>
          </RouterLink>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !filteredCompanies.length" description="暂无公司条目" :image-size="72" />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh, View } from '@element-plus/icons-vue'
import { api, type GraphNode, type GraphResponse } from '../api'

interface CompanyRow {
  key: string
  name: string
  ticker: string
  routeTicker: string
  reportCount: number
  conceptCount: number
  riskCount: number
  peopleCount: number
  updatedAt: string
}

const loading = ref(false)
const query = ref('')
const graph = ref<GraphResponse>({ nodes: [], edges: [] })
const route = useRoute()

const companies = computed(() => {
  const rows = new Map<string, CompanyRow>()
  for (const node of graph.value.nodes) {
    const key = companyKey(node)
    if (!key) continue
    const ticker = cleanTicker(node.ticker)
    const row = rows.get(key) || {
      key,
      name: node.company_short_name || node.company_name || (node.type === 'company-profile' ? node.label : key),
      ticker,
      routeTicker: routeTicker(ticker || key),
      reportCount: 0,
      conceptCount: 0,
      riskCount: 0,
      peopleCount: 0,
      updatedAt: node.updated_at || '',
    }
    if (node.type === 'company-profile') row.name = node.company_short_name || node.company_name || node.label
    if (ticker && !row.ticker) {
      row.ticker = ticker
      row.routeTicker = routeTicker(ticker)
    }
    if (node.type === 'annual-report') row.reportCount += 1
    else if (node.type === 'company-executive-profile') row.peopleCount += 1
    else if (node.type.startsWith('company-risk')) row.riskCount += 1
    else if (node.type !== 'company-profile') row.conceptCount += 1
    if (node.updated_at && node.updated_at > row.updatedAt) row.updatedAt = node.updated_at
    rows.set(key, row)
  }
  return [...rows.values()].sort((a, b) => b.reportCount - a.reportCount || a.name.localeCompare(b.name, 'zh-Hans-CN'))
})

const filteredCompanies = computed(() => {
  const text = query.value.trim().toLowerCase()
  if (!text) return companies.value
  return companies.value.filter((item) => `${item.name} ${item.ticker}`.toLowerCase().includes(text))
})

async function loadCompanies() {
  loading.value = true
  try {
    const response = await api.get<GraphResponse>('/api/graph/nodes', { params: { limit: 500 } })
    graph.value = response.data
  } finally {
    loading.value = false
  }
}

function companyKey(node: GraphNode) {
  return cleanTicker(node.ticker) || node.company_short_name || node.company_name || (node.type === 'company-profile' ? node.label : '')
}

function cleanTicker(value?: string) {
  return String(value || '').trim().toUpperCase()
}

function routeTicker(value: string) {
  const text = cleanTicker(value)
  if (/^(SH|SZ|BJ)\d{6}$/.test(text)) return text
  const suffix = text.match(/^(\d{6})\.(SH|SZ|BJ)$/)
  if (suffix) return `${suffix[2]}${suffix[1]}`
  if (/^6\d{5}$/.test(text)) return `SH${text}`
  if (/^[03]\d{5}$/.test(text)) return `SZ${text}`
  if (/^[48]\d{5}$/.test(text)) return `BJ${text}`
  return text
}

onMounted(() => {
  query.value = String(route.query.q || '')
  loadCompanies()
})

watch(
  () => route.query.q,
  (value) => {
    query.value = String(value || '')
  },
)
</script>
