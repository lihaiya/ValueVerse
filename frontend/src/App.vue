<template>
  <RouterView v-if="isAuthPage" />

  <div v-else class="app-shell">
    <aside class="sidebar">
      <RouterLink class="sidebar-brand" to="/dashboard">
        <span class="brand-icon">VI</span>
        <strong>valueverse</strong>
      </RouterLink>

      <nav class="sidebar-nav">
        <RouterLink class="nav-item home" :class="{ active: isActive('/dashboard') }" to="/dashboard">
          <el-icon><House /></el-icon>
          <strong>知识库首页</strong>
        </RouterLink>

        <p class="nav-label">索引</p>
        <RouterLink class="nav-item" :class="{ active: isActive('/wiki') }" to="/wiki">
          <el-icon><Collection /></el-icon>
          <strong>Wiki 条目</strong>
          <em>{{ wikiCount }}</em>
        </RouterLink>
        <RouterLink class="nav-item" :class="{ active: isActive('/companies') || isActive('/company') }" to="/companies">
          <el-icon><OfficeBuilding /></el-icon>
          <strong>公司全景</strong>
          <em>{{ companyCount }}</em>
        </RouterLink>
        <RouterLink class="nav-item" :class="{ active: isActive('/docs') }" to="/docs">
          <el-icon><Files /></el-icon>
          <strong>来源文档</strong>
          <em>{{ sourceCount }}</em>
        </RouterLink>
        <RouterLink class="nav-item" :class="{ active: isActive('/settings/domains') }" to="/settings/domains">
          <el-icon><SetUp /></el-icon>
          <strong>领域管理</strong>
          <em>{{ domainCount }}</em>
        </RouterLink>

        <p class="nav-label">工具</p>
        <RouterLink class="nav-item" :class="{ active: isActive('/graph') }" to="/graph">
          <el-icon><Share /></el-icon>
          <strong>知识图谱</strong>
        </RouterLink>
        <RouterLink class="nav-item" :class="{ active: isActive('/settings/llm') }" to="/settings/llm">
          <el-icon><Setting /></el-icon>
          <strong>模型配置</strong>
          <small>{{ modelLabel }}</small>
        </RouterLink>
      </nav>

      <RouterLink class="ai-entry" :class="{ active: isActive('/chat') }" to="/chat">
        <el-icon><ChatLineRound /></el-icon>
        <strong>AI 研究员</strong>
        <em>CHAT</em>
      </RouterLink>
    </aside>

    <main class="main-content">
      <div class="top-bar" :class="{ 'top-bar--account-only': !showTopSearch }">
        <el-input
          v-if="showTopSearch"
          v-model="globalQuery"
          clearable
          :prefix-icon="Search"
          :placeholder="topSearchPlaceholder"
          @keyup.enter="search"
        />
        <div class="account-bar">
          <el-select
            v-if="authStore.workspaces.length"
            :model-value="authStore.activeWorkspace?.id"
            size="small"
            class="workspace-select"
            @change="switchWorkspace"
          >
            <el-option v-for="workspace in authStore.workspaces" :key="workspace.id" :label="workspace.name" :value="workspace.id" />
          </el-select>
          <el-button class="account-entry" text :icon="UserFilled" title="用户中心" @click="router.push('/settings/account')">
            <span class="account-email">{{ authStore.user?.email }}</span>
          </el-button>
          <el-button size="small" :icon="SwitchButton" @click="logout">退出</el-button>
        </div>
      </div>

      <RouterView />
    </main>

    <nav class="mobile-nav">
      <RouterLink to="/wiki">Wiki</RouterLink>
      <RouterLink to="/companies">公司</RouterLink>
      <RouterLink to="/docs">文档</RouterLink>
      <RouterLink to="/settings/domains">领域</RouterLink>
      <RouterLink to="/graph">图谱</RouterLink>
      <RouterLink to="/chat">对话</RouterLink>
    </nav>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChatLineRound, Collection, Files, House, OfficeBuilding, Search, Setting, SetUp, Share, SwitchButton, UserFilled } from '@element-plus/icons-vue'
import { api, type Domain, type SourceDocument } from './api'
import { useAuthStore } from './stores/auth'
import { useConfigStore } from './stores/config'
import { useWikiStore } from './stores/wiki'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const configStore = useConfigStore()
const wikiStore = useWikiStore()
const globalQuery = ref('')
const sourceCount = ref(0)
const domainCount = ref(0)

const isAuthPage = computed(() => route.path.startsWith('/auth/'))
const showTopSearch = computed(() => route.path.startsWith('/wiki') || route.path.startsWith('/companies') || route.path.startsWith('/company'))
const topSearchPlaceholder = computed(() => {
  if (route.path.startsWith('/companies') || route.path.startsWith('/company')) return '搜索公司或证券代码'
  return '搜索 Wiki 条目、年报、风险事件或双向链接'
})
const modelLabel = computed(() => configStore.llmConfig?.model_name || '未配置')
const wikiCount = computed(() => wikiStore.nodes.length)
const companyCount = computed(() => new Set(wikiStore.nodes.filter((node) => node.type === 'company-profile').map((node) => node.title)).size)

function isActive(path: string) {
  return route.path === path || (path !== '/dashboard' && route.path.startsWith(path))
}

function search() {
  const targetPath = route.path.startsWith('/companies') || route.path.startsWith('/company') ? '/companies' : '/wiki'
  router.push({ path: targetPath, query: { q: globalQuery.value || undefined } })
}

async function switchWorkspace(workspaceId: string) {
  const workspace = authStore.workspaces.find((item) => item.id === workspaceId)
  if (!workspace) return
  authStore.switchWorkspace(workspace)
  wikiStore.nodes = []
  wikiStore.currentNode = null
  await loadShellData()
}

async function logout() {
  await authStore.logout()
  router.push('/auth/login')
}

async function loadShellData() {
  if (!authStore.isAuthenticated || isAuthPage.value) return
  const [sourcesResponse, domainsResponse] = await Promise.all([
    api.get<SourceDocument[]>('/api/sources/documents'),
    api.get<Domain[]>('/api/domains'),
    configStore.fetchConfig().catch(() => null),
    wikiStore.fetchNodes().catch(() => null),
  ])
  sourceCount.value = sourcesResponse.data.length
  domainCount.value = domainsResponse.data.length
}

onMounted(() => {
  syncTopSearch()
  loadShellData().catch(() => undefined)
})

watch(
  () => [route.path, route.query.q],
  () => syncTopSearch(),
)

watch(
  () => authStore.activeWorkspace?.id,
  () => loadShellData().catch(() => undefined),
)

function syncTopSearch() {
  if (!showTopSearch.value) {
    globalQuery.value = ''
    return
  }
  globalQuery.value = String(route.query.q || '')
}
</script>
