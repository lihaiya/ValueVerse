<template>
  <section class="graph-layout">
    <main class="section graph-main">
      <div class="section-header">
        <div>
          <h2>知识图谱</h2>
          <p class="muted">双击节点打开 Wiki；推断边会标记为 inferred，便于区分 LLM/人工关系</p>
        </div>
        <div class="toolbar">
          <el-button @click="loadGraph">刷新</el-button>
          <el-button @click="fitGraph">适配画布</el-button>
          <el-button @click="exportMermaid">Mermaid</el-button>
        </div>
      </div>

      <div class="graph-stats">
        <div>
          <strong>{{ graphData.nodes.length }}</strong>
          <span>节点</span>
        </div>
        <div>
          <strong>{{ graphData.edges.length }}</strong>
          <span>关系</span>
        </div>
        <div>
          <strong>{{ inferredEdgeCount }}</strong>
          <span>推断边</span>
        </div>
      </div>

      <div class="graph-canvas-wrap">
        <div v-if="!graphData.nodes.length" class="graph-empty">
          <strong>暂无可展示节点</strong>
          <span>上传并解析年报、公告或研究材料后，图谱会自动生成。</span>
        </div>
        <div ref="graphEl" class="x6-canvas" />
      </div>
    </main>

    <aside class="section graph-filter">
      <h2>过滤</h2>
      <el-select v-model="filters.type" clearable placeholder="节点类型" @change="loadGraph">
        <el-option label="年报" value="annual-report" />
        <el-option label="公司" value="company-profile" />
        <el-option label="概念" value="general-concept" />
        <el-option label="投资观点" value="investment-insight" />
        <el-option label="战略目标" value="company-strategy-goal" />
        <el-option label="业务财务" value="company-finance-segment" />
        <el-option label="风险" value="risk-event" />
        <el-option label="公司风险" value="company-risk-operation" />
        <el-option label="人员" value="personnel-profile" />
        <el-option label="业务分部" value="segment-analysis" />
      </el-select>
      <el-input v-model="filters.ticker" clearable placeholder="Ticker / 股票代码" @keyup.enter="loadGraph" />

      <div class="graph-legend">
        <span><i class="legend-dot report" />年报</span>
        <span><i class="legend-dot risk" />风险</span>
        <span><i class="legend-dot person" />人员</span>
        <span><i class="legend-dot segment" />业务</span>
        <span><i class="legend-dot concept" />概念</span>
      </div>

      <el-divider />
      <div v-if="selectedNode" class="graph-detail">
        <p class="eyebrow">Selected</p>
        <h3>{{ selectedNode.label }}</h3>
        <div class="detail-grid">
          <span>类型</span><strong>{{ selectedNode.type }}</strong>
          <span>股票</span><strong>{{ selectedNode.ticker || 'N/A' }}</strong>
          <span>年份</span><strong>{{ selectedNode.report_year || 'N/A' }}</strong>
          <span>目录</span><strong>{{ selectedNode.folder_path || '未归档' }}</strong>
          <span>状态</span><strong>{{ selectedNode.status || 'N/A' }}</strong>
        </div>
        <RouterLink :to="`/wiki/${selectedNode.id}`" target="_blank">
          <el-button type="primary" plain>打开 Wiki</el-button>
        </RouterLink>
      </div>
      <div v-else class="graph-detail empty-detail">
        <p class="eyebrow">Selected</p>
        <span>点击节点查看详情。</span>
      </div>

      <el-divider />
      <div class="graph-node-list">
        <button v-for="node in graphData.nodes" :key="node.id" type="button" class="graph-node-row" @click="focusNode(node.id)">
          <span>{{ node.label }}</span>
          <el-tag size="small">{{ node.type }}</el-tag>
        </button>
      </div>
    </aside>
  </section>
</template>

<script setup lang="ts">
import { Graph } from '@antv/x6'
import '@antv/x6/dist/index.css'
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api, type GraphNode, type GraphResponse } from '../api'

const router = useRouter()
const graphEl = ref<HTMLDivElement | null>(null)
const graph = ref<Graph | null>(null)
const graphData = ref<GraphResponse>({ nodes: [], edges: [] })
const selectedNode = ref<GraphNode | null>(null)
const filters = reactive({ type: '', ticker: '' })
const inferredEdgeCount = computed(() => graphData.value.edges.filter((edge) => Boolean(edge.metadata?.inferred)).length)

async function loadGraph() {
  const response = await api.get<GraphResponse>('/api/graph/nodes', { params: { ...filters } })
  graphData.value = response.data
  if (selectedNode.value && !graphData.value.nodes.some((node) => node.id === selectedNode.value?.id)) {
    selectedNode.value = null
  }
  await nextTick()
  renderGraph()
}

function renderGraph() {
  if (!graphEl.value) return
  graph.value?.dispose()
  graph.value = new Graph({
    container: graphEl.value,
    grid: { size: 16, visible: true, type: 'dot', args: { color: '#d7e1ea', thickness: 1 } },
    panning: true,
    mousewheel: { enabled: true, modifiers: ['ctrl', 'meta'] },
    interacting: { nodeMovable: true },
    background: { color: '#fbfdff' },
  })
  const positions = layoutNodes(graphData.value.nodes)
  const nodes = graphData.value.nodes.map((node) => ({
    id: node.id,
    shape: 'rect',
    x: positions[node.id]?.x || 80,
    y: positions[node.id]?.y || 80,
    width: 184,
    height: 62,
    label: compactLabel(node.label),
    attrs: {
      body: {
        rx: 8,
        ry: 8,
        fill: nodeFill(node),
        stroke: nodeStroke(node),
        strokeWidth: selectedNode.value?.id === node.id ? 2 : 1,
      },
      label: {
        fontSize: 12,
        fontWeight: 600,
        fill: '#24364b',
        textWrap: { width: 154, height: 38, ellipsis: true },
      },
    },
    data: node,
  }))
  const edges = graphData.value.edges.map((edge) => ({
    source: edge.source,
    target: edge.target,
    label: relationLabel(edge.relation_type),
    attrs: {
      line: {
        stroke: edge.metadata?.inferred ? '#9aa8b7' : '#4d7fb8',
        strokeWidth: Math.max(1, Math.min(3, edge.weight * 2)),
        strokeDasharray: edge.metadata?.inferred ? '5 5' : '',
        targetMarker: { name: 'classic', size: 7 },
      },
    },
    labels: [
      {
        attrs: {
          label: { fontSize: 11, fill: '#52616f' },
          body: { fill: '#ffffff', stroke: '#dfe6ee', rx: 4, ry: 4 },
        },
      },
    ],
  }))
  graph.value.fromJSON({ nodes, edges })
  graph.value.on('node:click', ({ node }) => {
    selectedNode.value = node.getData<GraphNode>()
    highlightNode(node.id)
  })
  graph.value.on('node:dblclick', ({ node }) => router.push(`/wiki/${node.id}`))
  fitGraph()
}

function layoutNodes(nodes: GraphNode[]) {
  const result: Record<string, { x: number; y: number }> = {}
  const groups = new Map<string, GraphNode[]>()
  for (const node of nodes) {
    const key = node.ticker || node.company_short_name || node.type || 'default'
    const group = groups.get(key) || []
    group.push(node)
    groups.set(key, group)
  }
  let groupIndex = 0
  for (const group of groups.values()) {
    group.sort((a, b) => (Number(a.report_year || 0) - Number(b.report_year || 0)) || a.label.localeCompare(b.label, 'zh-Hans-CN'))
    const column = groupIndex % 3
    const row = Math.floor(groupIndex / 3)
    group.forEach((node, index) => {
      result[node.id] = {
        x: 70 + column * 300 + index * 42,
        y: 70 + row * 210 + index * 92,
      }
    })
    groupIndex += 1
  }
  return result
}

function focusNode(nodeId: string) {
  const node = graphData.value.nodes.find((item) => item.id === nodeId)
  if (!node) return
  selectedNode.value = node
  highlightNode(nodeId)
  const cell = graph.value?.getCellById(nodeId)
  if (cell) graph.value?.centerCell(cell)
}

function highlightNode(nodeId: string) {
  for (const item of graph.value?.getNodes() || []) {
    item.attr('body/strokeWidth', item.id === nodeId ? 2 : 1)
    item.attr('body/stroke', item.id === nodeId ? '#1f6fb8' : nodeStroke(item.getData<GraphNode>()))
  }
}

function fitGraph() {
  if (!graph.value || !graphData.value.nodes.length) return
  graph.value.zoomToFit({ padding: 32, maxScale: 1 })
  graph.value.centerContent()
}

function compactLabel(label: string) {
  return label.length > 34 ? `${label.slice(0, 32)}...` : label
}

function relationLabel(value: string) {
  return {
    REPORT_YEAR_SEQUENCE: '年度序列',
    SAME_COMPANY_REPORT: '同公司',
    SAME_FOLDER: '同目录',
    SHARED_TAG: '同标签',
    RELATED_REPORT: '相关报告',
  }[value] || value
}

function nodeFill(node: GraphNode) {
  if (node.type.includes('risk')) return '#fff0f0'
  if (node.type.includes('person')) return '#fff8df'
  if (node.type.includes('segment')) return '#edf7f1'
  if (node.type.includes('concept') || node.type.includes('insight') || node.type.includes('strategy')) return '#fff8df'
  if (node.type.includes('annual')) return '#eef6ff'
  if (node.type.includes('company')) return '#f3f0ff'
  return '#f7f9fc'
}

function nodeStroke(node: GraphNode) {
  if (node.status?.includes('fallback') || node.status?.includes('warning')) return '#d9902f'
  if (node.type.includes('risk')) return '#d66a6a'
  if (node.type.includes('concept') || node.type.includes('insight') || node.type.includes('strategy')) return '#c5961b'
  if (node.type.includes('annual')) return '#5a91c9'
  return '#b8c5d3'
}

async function exportMermaid() {
  const labels = new Map(graphData.value.nodes.map((node) => [node.id, compactLabel(node.label).replaceAll('"', '')]))
  const lines = ['graph LR']
  for (const edge of graphData.value.edges) {
    lines.push(`  ${edge.source}[\"${labels.get(edge.source) || edge.source.slice(0, 6)}\"] -->|${relationLabel(edge.relation_type)}| ${edge.target}[\"${labels.get(edge.target) || edge.target.slice(0, 6)}\"]`)
  }
  await ElMessageBox.alert(lines.join('\n'), 'Mermaid 源码')
}

onMounted(loadGraph)
</script>
