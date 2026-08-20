<template>
  <section v-if="node" class="wiki-reader">
    <header class="reader-header">
      <div class="reader-title-block">
        <div class="breadcrumb-row">
          <el-button :icon="Back" text class="return-button" @click="goParent">返回上级</el-button>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item>
              <RouterLink to="/wiki">知识库总览</RouterLink>
            </el-breadcrumb-item>
            <el-breadcrumb-item v-if="companyLabel">
              <RouterLink :to="companyTarget">{{ companyLabel }}</RouterLink>
            </el-breadcrumb-item>
            <el-breadcrumb-item>
              <RouterLink :to="parentTarget">{{ typeLabel(node.type) }}</RouterLink>
            </el-breadcrumb-item>
            <el-breadcrumb-item>{{ node.title }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="reader-title-row">
          <h1>{{ node.title }}</h1>
          <el-tooltip :content="isBookmarked ? '取消书签' : '加入书签'">
            <el-button :icon="isBookmarked ? StarFilled : Star" circle text @click="toggleBookmark" />
          </el-tooltip>
        </div>
        <YamlMetaBadge :meta="node.yaml_meta" />
      </div>
    </header>

    <div class="reader-grid" :class="{ 'nav-collapsed': navCollapsed }">
      <aside v-if="!navCollapsed" class="reader-side">
        <div class="reader-side-head">
          <strong>页面目录</strong>
          <el-button :icon="Fold" text size="small" class="reader-nav-toggle" @click="navCollapsed = true">收起</el-button>
        </div>
        <WikiTree :node="node" :link-map="wikiStore.titleMap" @jump-heading="scrollToHeading" />
        <el-collapse v-model="expandedPanels" class="reader-local-nav">
          <el-collapse-item title="最近浏览" name="recent">
            <div v-if="recentEntries.length" class="local-entry-list">
              <button v-for="entry in recentEntries" :key="entry.id" :title="entry.title" class="local-entry" type="button" @click="openEntry(entry)">
                <strong>{{ entry.title }}</strong>
                <span>{{ entry.company || '未归属企业' }} · {{ entry.tag || typeLabel(entry.type) }}</span>
              </button>
              <el-button text size="small" @click="clearRecent">清空记录</el-button>
            </div>
            <el-empty v-else description="暂无浏览记录" :image-size="56" />
          </el-collapse-item>

          <el-collapse-item title="我的书签" name="bookmarks">
            <div v-if="bookmarkEntries.length" class="local-entry-list">
              <div v-for="entry in bookmarkEntries" :key="entry.id" class="bookmark-entry">
                <button :title="entry.title" class="local-entry" type="button" @click="openEntry(entry)">
                  <strong>{{ entry.title }}</strong>
                  <span>{{ entry.company || '未归属企业' }}</span>
                </button>
                <div class="bookmark-actions">
                  <el-button text size="small" @click="renameBookmark(entry)">重命名</el-button>
                  <el-button text size="small" type="danger" @click="deleteBookmark(entry.id)">删除</el-button>
                </div>
              </div>
            </div>
            <el-empty v-else description="暂无书签" :image-size="56" />
          </el-collapse-item>
        </el-collapse>
      </aside>

      <main class="reader-main">
        <div v-if="navCollapsed" class="reader-main-toolbar">
          <el-button :icon="Expand" text size="small" class="reader-nav-toggle" @click="navCollapsed = false">展开目录</el-button>
        </div>
        <MarkdownRenderer :content="node.content_md || ''" :link-map="wikiStore.titleMap" @missing-link="onMissingLink" />
        <section v-if="relatedGroups.length" class="related-concepts">
          <h3>相关概念</h3>
          <div v-for="group in relatedGroups" :key="group.key" class="related-group">
            <strong>{{ group.label }}</strong>
            <a
              v-for="concept in group.items"
              :key="concept.id"
              :href="`/wiki/${concept.id}`"
              target="_blank"
              rel="noopener"
              class="concept-row"
            >
              <span>{{ concept.title }}</span>
              <small>— {{ concept.description }}</small>
            </a>
          </div>
        </section>
        <SourceEvidencePanel :node-id="node.id" />
        <RawDocumentViewer :node-id="node.id" />
      </main>
    </div>

    <MemoryToolbar :node="node" @updated="loadNode" @deleted="handleNodeDeleted" />
  </section>
  <el-alert v-else-if="error" type="error" :title="error" show-icon :closable="false" />
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Back, Expand, Fold, Star, StarFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { WikiNode, WikiNodeListItem } from '../api'
import MarkdownRenderer from '../components/MarkdownRenderer.vue'
import MemoryToolbar from '../components/MemoryToolbar.vue'
import RawDocumentViewer from '../components/RawDocumentViewer.vue'
import SourceEvidencePanel from '../components/SourceEvidencePanel.vue'
import WikiTree from '../components/WikiTree.vue'
import YamlMetaBadge from '../components/YamlMetaBadge.vue'
import { useWikiStore } from '../stores/wiki'

interface ReaderEntry {
  id: string
  title: string
  type: string
  company: string
  tag: string
  visited_at: string
}

interface RelatedConcept {
  id: string
  title: string
  type: string
  description: string
}

const RECENT_KEY = 'valueverse:recent'
const BOOKMARK_KEY = 'valueverse:bookmarks'

const props = defineProps<{ id: string }>()
const router = useRouter()
const wikiStore = useWikiStore()
const node = ref<WikiNode | null>(null)
const error = ref('')
const recentEntries = ref<ReaderEntry[]>([])
const bookmarkEntries = ref<ReaderEntry[]>([])
const expandedPanels = ref(['recent', 'bookmarks'])
const navCollapsed = ref(false)

const isBookmarked = computed(() => Boolean(node.value && bookmarkEntries.value.some((entry) => entry.id === node.value?.id)))
const companyLabel = computed(() => companyOf(node.value))
const companyTarget = computed(() => {
  const ticker = String(node.value?.yaml_meta?.ticker || '').trim()
  if (ticker) return `/company/${ticker}`
  return { path: '/wiki', query: { q: companyLabel.value } }
})
const parentTarget = computed(() => ({ path: '/wiki', query: { q: node.value?.type || '' } }))

const relatedGroups = computed(() => {
  if (!node.value) return []
  const titles = Array.from(new Set([...relatedFromMeta(node.value), ...extractLinks(node.value.content_md || '')])).filter(
    (title) => title && title !== node.value?.title,
  )
  const edgeConcepts = (node.value.related_nodes || [])
    .map((item) => ({
      id: String(item.id || ''),
      title: String(item.title || ''),
      type: String(item.type || 'general-concept'),
      description: String(item.description || conceptDescription({ type: String(item.type || '') } as WikiNodeListItem)),
    }))
    .filter((item) => item.id && item.title && item.title !== node.value?.title)
  const linkedConcepts = titles
    .map((title) => {
      const linked = wikiStore.nodes.find((item) => item.title === title)
      if (!linked) return null
      return {
        id: linked.id,
        title: linked.title,
        type: linked.type,
        description: conceptDescription(linked),
      }
    })
    .filter(Boolean) as RelatedConcept[]
  const seenConceptIds = new Set<string>()
  const concepts = [...edgeConcepts, ...linkedConcepts].filter((item) => {
    if (seenConceptIds.has(item.id)) return false
    seenConceptIds.add(item.id)
    return true
  })

  const groups = [
    { key: 'people', label: '核心人物', match: (type: string) => type === 'company-executive-profile', items: [] as RelatedConcept[] },
    { key: 'finance', label: '业务财务', match: (type: string) => type === 'company-finance-segment', items: [] as RelatedConcept[] },
    { key: 'strategy', label: '战略目标', match: (type: string) => type === 'company-strategy-goal', items: [] as RelatedConcept[] },
    { key: 'risk', label: '风险事件', match: (type: string) => type.startsWith('company-risk'), items: [] as RelatedConcept[] },
    { key: 'news', label: '关联新闻', match: (type: string) => type.startsWith('company-news'), items: [] as RelatedConcept[] },
    { key: 'concept', label: '概念', match: (type: string) => ['general-concept', 'investment-insight', 'annual-report', 'financial-trend', 'segment-analysis'].includes(type), items: [] as RelatedConcept[] },
  ]

  for (const concept of concepts) {
    const group = groups.find((item) => item.match(concept.type)) || groups.at(-1)
    if (group && group.items.length < 3) group.items.push(concept)
  }
  return groups.filter((group) => group.items.length)
})

async function loadNode() {
  try {
    error.value = ''
    await wikiStore.fetchNodes()
    node.value = await wikiStore.fetchNode(props.id)
    recordRecent(node.value)
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Wiki 条目读取失败'
  }
}

async function handleNodeDeleted() {
  node.value = null
  await wikiStore.fetchNodes()
  router.push('/wiki')
}

function goParent() {
  router.push(parentTarget.value)
}

function scrollToHeading(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function toggleBookmark() {
  if (!node.value) return
  if (isBookmarked.value) {
    bookmarkEntries.value = bookmarkEntries.value.filter((entry) => entry.id !== node.value?.id)
    ElMessage.success('已取消书签')
  } else {
    bookmarkEntries.value = [entryFromNode(node.value), ...bookmarkEntries.value.filter((entry) => entry.id !== node.value?.id)]
    ElMessage.success('已加入书签')
  }
  writeEntries(BOOKMARK_KEY, bookmarkEntries.value)
}

async function renameBookmark(entry: ReaderEntry) {
  const result = await ElMessageBox.prompt('输入新的书签名称', '重命名书签', { inputValue: entry.title })
  const nextName = String(result.value || '').trim()
  if (!nextName) return
  bookmarkEntries.value = bookmarkEntries.value.map((item) => (item.id === entry.id ? { ...item, title: nextName } : item))
  writeEntries(BOOKMARK_KEY, bookmarkEntries.value)
}

function deleteBookmark(id: string) {
  bookmarkEntries.value = bookmarkEntries.value.filter((entry) => entry.id !== id)
  writeEntries(BOOKMARK_KEY, bookmarkEntries.value)
}

function clearRecent() {
  recentEntries.value = []
  writeEntries(RECENT_KEY, [])
}

function openEntry(entry: ReaderEntry) {
  window.open(router.resolve(`/wiki/${entry.id}`).href, '_blank', 'noopener')
}

function recordRecent(current: WikiNode) {
  const entry = entryFromNode(current)
  recentEntries.value = [entry, ...recentEntries.value.filter((item) => item.id !== entry.id)].slice(0, 20)
  writeEntries(RECENT_KEY, recentEntries.value)
}

function entryFromNode(current: WikiNode): ReaderEntry {
  return {
    id: current.id,
    title: current.title,
    type: current.type,
    company: companyOf(current),
    tag: typeLabel(current.type),
    visited_at: new Date().toISOString(),
  }
}

function companyOf(current: WikiNode | null) {
  if (!current) return ''
  return String(current.yaml_meta.company_name || current.yaml_meta.company || current.yaml_meta.ticker || '').trim()
}

function typeLabel(type: string) {
  const labels: Record<string, string> = {
    'company-overview': '发展全景',
    'company-finance-segment': '财务数据',
    'company-strategy-goal': '战略目标',
    'company-executive-profile': '核心管理层',
    'company-risk-operation': '风险与合规',
    'company-risk-legal': '风险与合规',
    'company-risk-compliance': '风险与合规',
    'company-news-official': '外部新闻',
    'company-news-mainstream': '外部新闻',
    'company-news-social': '外部新闻',
  }
  return labels[type] || type
}

function conceptDescription(item: WikiNodeListItem) {
  if (item.type === 'company-executive-profile') return '与当前词条关联的管理层人物'
  if (item.type === 'company-finance-segment') return '与当前词条关联的业务线或财务指标'
  if (item.type === 'company-strategy-goal') return '与当前词条关联的战略规划节点'
  if (item.type.startsWith('company-risk')) return '与当前词条关联的风险事项'
  if (item.type.startsWith('company-news')) return '与当前词条关联的外部新闻'
  return `${typeLabel(item.type)}相关词条`
}

function relatedFromMeta(current: WikiNode) {
  return Array.isArray(current.yaml_meta.related) ? current.yaml_meta.related.map(String) : []
}

function extractLinks(markdown: string) {
  return Array.from(markdown.matchAll(/\[\[([^\]]+)]]/g)).map((match) => match[1].trim())
}

function onMissingLink(title: string) {
  ElMessage.info(`缺失节点建议已记录：${title}`)
}

function readEntries(key: string) {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '[]')
    return Array.isArray(parsed) ? (parsed as ReaderEntry[]) : []
  } catch {
    return []
  }
}

function writeEntries(key: string, entries: ReaderEntry[]) {
  localStorage.setItem(key, JSON.stringify(entries))
}

recentEntries.value = readEntries(RECENT_KEY)
bookmarkEntries.value = readEntries(BOOKMARK_KEY)
watch(() => props.id, loadNode, { immediate: true })
</script>
