<template>
  <section class="home-hero">
    <div>
      <h1>valueverse</h1>
      <p>{{ subtitle }}</p>
      <div class="hero-actions">
        <RouterLink to="/chat"><el-button type="warning" round>🧑‍💼 问 AI 研究员</el-button></RouterLink>
        <RouterLink to="/graph"><el-button class="ghost-button" round>🕸️ 探索知识图谱</el-button></RouterLink>
      </div>
    </div>
    <div class="mini-graph" aria-hidden="true">
      <span v-for="dot in miniDots" :key="dot.id" :style="{ left: dot.x, top: dot.y, background: dot.color }" />
    </div>
  </section>

  <div class="home-metrics">
    <RouterLink class="home-metric letter" to="/docs">
      <strong><span>📄</span>{{ sourceCount }}</strong>
      <em>来源文档</em>
    </RouterLink>
    <RouterLink class="home-metric concept" to="/wiki">
      <strong><span>💡</span>{{ conceptCount }}</strong>
      <em>概念</em>
    </RouterLink>
    <RouterLink class="home-metric company" to="/companies">
      <strong><span>🏢</span>{{ companyCount }}</strong>
      <em>公司</em>
    </RouterLink>
    <RouterLink class="home-metric interview" to="/graph">
      <strong><span>🕸️</span>{{ edgeCount }}</strong>
      <em>图谱关系</em>
    </RouterLink>
  </div>

  <section class="home-search">
    <el-input v-model="query" clearable size="large" placeholder="搜索概念、公司、人物、年报..." @keyup.enter="search" />
  </section>

  <section class="topic-panel concept-panel">
    <div class="topic-head">
      <h2>概念</h2>
      <span>TOP 15</span>
    </div>
    <div v-if="topTags.length" class="chip-wrap">
      <RouterLink v-for="item in topTags" :key="item.name" class="topic-chip gold-chip" :to="{ path: '/wiki', query: { q: item.name } }">
        {{ item.name }} <em>{{ item.count }}</em>
      </RouterLink>
    </div>
    <el-empty v-else description="暂无已抽取概念" :image-size="64" />
  </section>

  <section class="topic-panel company-panel">
    <div class="topic-head">
      <h2>公司</h2>
      <span>TOP 15</span>
    </div>
    <div class="chip-wrap">
      <RouterLink v-for="item in topCompanies" :key="item.name" class="topic-chip green-chip" :to="{ path: '/wiki', query: { q: item.name } }">
        {{ item.name }} <em>{{ item.count }}</em>
      </RouterLink>
    </div>
  </section>

  <section class="section">
    <div class="section-header">
      <div>
        <p class="eyebrow">Recently Compiled</p>
        <h2>最近条目</h2>
      </div>
      <RouterLink to="/docs"><el-button>文档管理</el-button></RouterLink>
    </div>
    <el-table :data="nodes" stripe>
      <el-table-column prop="title" label="标题" min-width="260" />
      <el-table-column prop="type" label="类型" width="170" />
      <el-table-column prop="analysis_status" label="状态" width="160" />
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <RouterLink :to="`/wiki/${row.id}`"><el-button size="small">查看</el-button></RouterLink>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, type GraphResponse, type SourceDocument, type WikiNodeListItem } from '../api'

const router = useRouter()
const nodes = ref<WikiNodeListItem[]>([])
const sources = ref<SourceDocument[]>([])
const graph = ref<GraphResponse>({ nodes: [], edges: [] })
const query = ref('')

const sourceCount = computed(() => sources.value.length)
const edgeCount = computed(() => graph.value.edges.length)
const conceptNodes = computed(() => nodes.value.filter(isConceptNode))
const conceptCount = computed(() => conceptNodes.value.length)
const companyCount = computed(() => nodes.value.filter((node) => node.type.includes('company') || node.type.includes('annual')).length)
const subtitle = computed(() => `${sourceCount.value} 份资料，${nodes.value.length} 个知识页面，${edgeCount.value} 条关系`)
const topTags = computed(() => topByName(conceptNodes.value.map((node) => node.title), 15))
const topCompanies = computed(() => topByName(graph.value.nodes.map((node) => node.company_short_name || node.ticker || node.label.split(/\s|年/)[0]).filter(Boolean), 15))
const miniDots = Array.from({ length: 70 }, (_, index) => ({
  id: index,
  x: `${44 + Math.cos(index * 1.9) * (index % 31) * 1.5}%`,
  y: `${50 + Math.sin(index * 1.4) * (index % 23) * 1.3}%`,
  color: ['#568de5', '#c5961b', '#47956a', '#7e5fad', '#c2604a'][index % 5],
}))

async function loadHome() {
  const [nodesResponse, sourcesResponse, graphResponse] = await Promise.all([
    api.get<WikiNodeListItem[]>('/api/wiki/nodes', { params: { limit: 200 } }),
    api.get<SourceDocument[]>('/api/sources/documents'),
    api.get<GraphResponse>('/api/graph/nodes'),
  ])
  nodes.value = nodesResponse.data
  sources.value = sourcesResponse.data
  graph.value = graphResponse.data
}

function search() {
  router.push({ path: '/wiki', query: { q: query.value } })
}

function topByName(names: string[], limit: number) {
  const counts = new Map<string, number>()
  for (const name of names) {
    const clean = String(name || '').trim()
    if (!clean) continue
    counts.set(clean, (counts.get(clean) || 0) + 1)
  }
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'zh-Hans-CN'))
    .slice(0, limit)
}

function isConceptNode(node: WikiNodeListItem) {
  const type = String(node.type || '').toLowerCase()
  if (!type || ['annual-report', 'company-profile', 'company-overview'].includes(type)) return false
  return (
    type.includes('concept') ||
    type.includes('insight') ||
    type.includes('segment') ||
    type.includes('finance') ||
    type.includes('strategy') ||
    type.includes('goal') ||
    type.includes('risk') ||
    type.includes('news') ||
    type.includes('executive') ||
    type.includes('trend')
  )
}

onMounted(loadHome)
</script>
