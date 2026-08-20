<template>
  <section class="section company-header">
    <div>
      <p class="eyebrow">Company Panorama</p>
      <h2>{{ companyName }} 公司全景</h2>
      <p class="muted">{{ displayTicker }} · 基于已入库 Wiki 节点聚合年度材料、业务、风险与高管轨迹</p>
    </div>
    <div class="toolbar">
      <RouterLink to="/companies"><el-button>公司列表</el-button></RouterLink>
      <RouterLink :to="{ path: '/wiki', query: { q: companyName } }"><el-button>相关 Wiki</el-button></RouterLink>
      <RouterLink to="/graph"><el-button type="primary">加入图谱</el-button></RouterLink>
    </div>
  </section>

  <div class="company-summary-grid">
    <div class="company-summary-item">
      <strong>{{ annualReports.length }}</strong>
      <span>年度材料</span>
    </div>
    <div class="company-summary-item">
      <strong>{{ segmentNodes.length }}</strong>
      <span>业务/财务概念</span>
    </div>
    <div class="company-summary-item">
      <strong>{{ riskNodes.length }}</strong>
      <span>风险事项</span>
    </div>
    <div class="company-summary-item">
      <strong>{{ peopleNodes.length }}</strong>
      <span>高管人物</span>
    </div>
  </div>

  <section class="section">
    <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" />
    <el-tabs v-else v-model="activeTab" v-loading="loading">
      <el-tab-pane label="年度材料" name="financial">
        <div ref="trendChart" class="chart-box" />
        <el-table :data="annualReports" stripe>
          <el-table-column label="年度" width="100">
            <template #default="{ row }">{{ row.report_year || '未标注' }}</template>
          </el-table-column>
          <el-table-column prop="label" label="条目" min-width="260" />
          <el-table-column prop="status" label="状态" width="130" />
          <el-table-column prop="folder_path" label="目录" min-width="180" />
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <RouterLink :to="`/wiki/${row.id}`"><el-button size="small">打开</el-button></RouterLink>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!annualReports.length" description="暂无年度材料" :image-size="64" />
      </el-tab-pane>

      <el-tab-pane label="子业务映射" name="segments">
        <div ref="segmentChart" class="chart-box segment-chart-box" />
        <div class="quote-grid">
          <RouterLink v-for="node in segmentNodes" :key="node.id" :to="`/wiki/${node.id}`" class="quote-card">
            <strong>{{ node.label }}</strong>
            <span>{{ typeLabel(node.type) }}</span>
          </RouterLink>
        </div>
        <el-empty v-if="!segmentNodes.length" description="暂无业务/财务概念" :image-size="64" />
      </el-tab-pane>

      <el-tab-pane label="风险清单" name="risks">
        <el-timeline v-if="riskNodes.length">
          <el-timeline-item v-for="risk in riskNodes" :key="risk.id" :timestamp="risk.report_year ? String(risk.report_year) : '未标注'" type="warning">
            <RouterLink :to="`/wiki/${risk.id}`">{{ risk.label }}</RouterLink>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无风险事项" :image-size="64" />
      </el-tab-pane>

      <el-tab-pane label="高管轨迹" name="people">
        <div class="people-grid">
          <RouterLink v-for="person in peopleNodes" :key="person.id" :to="`/wiki/${person.id}`" class="person-card">
            <strong>{{ person.label }}</strong>
            <span>{{ typeLabel(person.type) }}</span>
            <small>{{ person.company_short_name || companyName }}</small>
          </RouterLink>
        </div>
        <el-empty v-if="!peopleNodes.length" description="暂无高管人物" :image-size="64" />
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { api, type GraphNode, type GraphResponse } from '../api'

const props = defineProps<{ ticker: string }>()
const graph = ref<GraphResponse>({ nodes: [], edges: [] })
const loading = ref(false)
const error = ref('')
const trendChart = ref<HTMLDivElement | null>(null)
const segmentChart = ref<HTMLDivElement | null>(null)
const activeTab = ref('financial')

const nodes = computed(() => graph.value.nodes)
const companyNode = computed(() => nodes.value.find((node) => node.type === 'company-profile') || nodes.value[0])
const companyName = computed(() => companyNode.value?.company_short_name || companyNode.value?.company_name || companyNode.value?.label || props.ticker)
const displayTicker = computed(() => routeTicker(companyNode.value?.ticker || props.ticker))
const annualReports = computed(() => nodesByType(['annual-report']).sort((a, b) => Number(b.report_year || 0) - Number(a.report_year || 0)))
const segmentNodes = computed(() => nodes.value.filter((node) => ['company-finance-segment', 'segment-analysis', 'financial-trend', 'investment-insight'].includes(node.type)))
const riskNodes = computed(() => nodes.value.filter((node) => node.type.startsWith('company-risk')))
const peopleNodes = computed(() => nodes.value.filter((node) => node.type === 'company-executive-profile'))
const segmentRows = computed(() => {
  const rows = segmentNodes.value.slice(0, 12).map((node) => ({ name: node.label, value: 1 }))
  return rows.length ? rows : [{ name: '暂无数据', value: 1 }]
})

async function loadCompany() {
  loading.value = true
  error.value = ''
  try {
    const response = await api.get<GraphResponse>('/api/graph/nodes', { params: { ticker: props.ticker, limit: 500 } })
    graph.value = response.data
    await nextTick()
    renderCharts()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '公司全景读取失败'
  } finally {
    loading.value = false
  }
}

function nodesByType(types: string[]) {
  return nodes.value.filter((node) => types.includes(node.type))
}

function renderCharts() {
  if (trendChart.value) {
    const chart = echarts.getInstanceByDom(trendChart.value) || echarts.init(trendChart.value)
    const years = annualReports.value.map((row) => String(row.report_year || '未标注'))
    chart.resize()
    chart.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 44, right: 24, top: 24, bottom: 34 },
      xAxis: { type: 'category', data: years },
      yAxis: { type: 'value', minInterval: 1 },
      series: [{ name: '年度材料', type: 'bar', data: annualReports.value.map(() => 1), itemStyle: { color: '#3b7dd8' } }],
    })
  }
  if (segmentChart.value) {
    const chart = echarts.getInstanceByDom(segmentChart.value) || echarts.init(segmentChart.value)
    chart.resize()
    chart.setOption({
      tooltip: { trigger: 'item' },
      legend: {
        type: 'scroll',
        bottom: 0,
        left: 'center',
        icon: 'circle',
        itemWidth: 8,
        itemHeight: 8,
        textStyle: { color: '#6b7785', fontSize: 12 },
      },
      series: [{
        type: 'pie',
        radius: ['34%', '58%'],
        center: ['50%', '42%'],
        bottom: 56,
        avoidLabelOverlap: true,
        minShowLabelAngle: 6,
        label: { color: '#303133', fontSize: 12, formatter: '{b}', overflow: 'break', width: 120 },
        labelLine: { length: 12, length2: 8 },
        data: segmentRows.value,
      }],
    })
  }
}

function routeTicker(value: string) {
  const text = String(value || '').trim().toUpperCase()
  if (/^(SH|SZ|BJ)\d{6}$/.test(text)) return text
  const suffix = text.match(/^(\d{6})\.(SH|SZ|BJ)$/)
  if (suffix) return `${suffix[2]}${suffix[1]}`
  if (/^6\d{5}$/.test(text)) return `SH${text}`
  if (/^[03]\d{5}$/.test(text)) return `SZ${text}`
  if (/^[48]\d{5}$/.test(text)) return `BJ${text}`
  return text
}

function typeLabel(type: string) {
  const labels: Record<string, string> = {
    'company-finance-segment': '业务财务',
    'segment-analysis': '业务分析',
    'financial-trend': '财务趋势',
    'investment-insight': '投资概念',
    'company-executive-profile': '核心管理层',
  }
  if (type.startsWith('company-risk')) return '风险事项'
  return labels[type] || type
}

watch(() => props.ticker, loadCompany)
watch(activeTab, () => nextTick(renderCharts))
onMounted(loadCompany)
</script>
