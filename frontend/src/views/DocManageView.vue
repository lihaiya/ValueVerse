<template>
  <section class="doc-workbench">
    <div class="ingest-panel section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Ingestion</p>
          <h2>文档解析管道</h2>
        </div>
        <div class="toolbar">
          <el-button :icon="Refresh" :loading="loading" @click="loadAll">刷新</el-button>
          <el-button :icon="Delete" type="danger" plain @click="clearKnowledge">清空知识</el-button>
        </div>
      </div>

      <el-form label-position="top" class="parse-config">
        <el-form-item label="文档目录">
          <el-input v-model="selectedFolderPath" clearable placeholder="例如：用友年报/年度报告">
            <template #append>上传目标</template>
          </el-input>
        </el-form-item>
        <el-form-item label="解析领域">
          <el-select v-model="selectedDomainId" placeholder="选择领域" @change="syncDomainPacks">
            <el-option v-for="domain in domains" :key="domain.id" :label="domain.name" :value="domain.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="领域包">
          <el-checkbox-group v-model="selectedPackIds" class="pack-checks compact">
            <el-checkbox v-for="pack in activePacks" :key="pack.id" :label="pack.id">
              <span>{{ pack.name }}</span>
              <small>{{ pack.slug }}</small>
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>

      <el-upload drag multiple :http-request="upload" :show-file-list="false" accept=".pdf,.docx,.txt,.md">
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽或选择 PDF、DOCX、TXT、MD</div>
        <template #tip>
          <span class="upload-tip">当前目录：{{ selectedFolderPath || '未归档' }}</span>
        </template>
      </el-upload>

      <el-steps
        v-if="lastTask"
        class="parse-steps"
        :active="parseActive"
        :process-status="lastTask.status === 'failed' ? 'error' : 'process'"
        finish-status="success"
      >
        <el-step title="Parser" />
        <el-step title="LLM Extract" />
        <el-step title="Wiki 入库" />
        <el-step title="Memory" />
      </el-steps>
      <el-alert
        v-if="lastTask?.message"
        class="task-message"
        :type="lastTask.status === 'failed' || lastTask.message.includes('fallback') ? 'warning' : 'success'"
        :title="lastTask.message"
        show-icon
      />
    </div>

    <aside class="folder-panel section">
      <div class="section-header compact">
        <div>
          <p class="eyebrow">Folders</p>
          <h2>文档目录</h2>
        </div>
      </div>
      <div class="folder-list">
        <button
          v-for="folder in folderRows"
          :key="folder.key"
          class="folder-row"
          :class="{ active: currentFolder === folder.key }"
          type="button"
          @click="selectFolder(folder.key)"
        >
          <span>
            <strong>{{ folder.label }}</strong>
            <small>{{ folder.path }}</small>
          </span>
          <el-tag size="small">{{ folder.count }}</el-tag>
        </button>
      </div>
    </aside>

    <div class="source-ledger section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Source Ledger</p>
          <h2>来源文档</h2>
        </div>
        <el-tag>{{ filteredSources.length }} / {{ sources.length }} 份</el-tag>
      </div>
      <el-table :data="filteredSources" stripe>
        <el-table-column label="文件" min-width="280">
          <template #default="{ row }">
            <div class="table-title">
              <strong>{{ row.filename }}</strong>
              <span>{{ formatBytes(row.size_bytes) }} · {{ row.mime_type || 'unknown' }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="目录" min-width="160">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ sourceFolder(row) || '未归档' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="解析器" min-width="130">
          <template #default="{ row }">{{ row.document_metadata.parser_name || 'parser' }}</template>
        </el-table-column>
        <el-table-column label="质量" min-width="190">
          <template #default="{ row }">
            <el-tag :type="needsOcr(row) ? 'warning' : 'success'" size="small">
              {{ needsOcr(row) ? '建议 OCR' : '文本可用' }}
            </el-tag>
            <span class="quality-note">{{ qualityNote(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-tag :type="sourceStatusType(row)" size="small">{{ sourceStatusLabel(row) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button size="small" plain :disabled="isProcessing(row) || isDeleting(row)" @click="reparseSource(row)">
              重解析
            </el-button>
            <el-button v-if="isProcessing(row)" size="small" type="warning" plain @click="cancelSource(row)">
              停止
            </el-button>
            <el-button size="small" type="danger" plain :disabled="isDeleting(row)" @click="deleteSource(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </section>

  <section class="section">
    <div class="section-header">
      <div>
        <p class="eyebrow">Wiki Output</p>
        <h2>最近解析</h2>
      </div>
    </div>
    <el-table :data="nodes" stripe>
      <el-table-column prop="title" label="标题" min-width="260" />
      <el-table-column prop="type" label="类型" width="180" />
      <el-table-column prop="analysis_status" label="状态" width="220" />
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <RouterLink :to="`/wiki/${row.id}`" target="_blank"><el-button size="small">阅读</el-button></RouterLink>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { UploadRequestOptions } from 'element-plus'
import { Delete, Refresh, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, type Domain, type DomainPack, type SourceDocument, type WikiNodeListItem } from '../api'

interface ParseTask {
  id: string
  status: string
  progress: number
  message?: string
}

interface FolderRow {
  key: string
  label: string
  path: string
  count: number
}

const ALL_FOLDERS = '__all__'
const UNFILED_FOLDER = '__unfiled__'

const nodes = ref<WikiNodeListItem[]>([])
const domains = ref<Domain[]>([])
const packs = ref<DomainPack[]>([])
const sources = ref<SourceDocument[]>([])
const selectedDomainId = ref('')
const selectedPackIds = ref<string[]>([])
const selectedFolderPath = ref('')
const currentFolder = ref(ALL_FOLDERS)
const lastTask = ref<ParseTask | null>(null)
const loading = ref(false)
const activeTaskIds = ref<string[]>([])
let pollTimer: number | undefined

const activePacks = computed(() => packs.value.filter((pack) => pack.is_active))
const selectedDomain = computed(() => domains.value.find((domain) => domain.id === selectedDomainId.value))
const parseActive = computed(() => (lastTask.value?.status === 'completed' ? 4 : ['failed', 'cancelled'].includes(lastTask.value?.status || '') ? 1 : 2))
const folderRows = computed<FolderRow[]>(() => {
  const counts = new Map<string, number>()
  for (const source of sources.value) {
    const folder = sourceFolder(source)
    counts.set(folder, (counts.get(folder) || 0) + 1)
  }
  const rows: FolderRow[] = [
    { key: ALL_FOLDERS, label: '全部文档', path: '所有目录', count: sources.value.length },
  ]
  for (const [folder, count] of [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0], 'zh-Hans-CN'))) {
    rows.push({
      key: folder || UNFILED_FOLDER,
      label: folder ? folder.split('/').at(-1) || folder : '未归档',
      path: folder || '无目录',
      count,
    })
  }
  return rows
})
const filteredSources = computed(() => {
  if (currentFolder.value === ALL_FOLDERS) return sources.value
  if (currentFolder.value === UNFILED_FOLDER) return sources.value.filter((source) => !sourceFolder(source))
  return sources.value.filter((source) => sourceFolder(source) === currentFolder.value)
})

async function loadAll() {
  loading.value = true
  try {
    const [nodesResponse, domainsResponse, packsResponse, sourcesResponse] = await Promise.all([
      api.get<WikiNodeListItem[]>('/api/wiki/nodes'),
      api.get<Domain[]>('/api/domains'),
      api.get<DomainPack[]>('/api/domain-packs'),
      api.get<SourceDocument[]>('/api/sources/documents'),
    ])
    nodes.value = nodesResponse.data
    domains.value = domainsResponse.data
    packs.value = packsResponse.data
    sources.value = sourcesResponse.data
    if (!selectedDomainId.value && domains.value.length) {
      selectedDomainId.value = domains.value[0].id
      syncDomainPacks()
    }
  } finally {
    loading.value = false
  }
}

function selectFolder(key: string) {
  currentFolder.value = key
  if (key === ALL_FOLDERS || key === UNFILED_FOLDER) {
    selectedFolderPath.value = ''
    return
  }
  selectedFolderPath.value = key
}

function syncDomainPacks() {
  selectedPackIds.value = selectedDomain.value?.domain_packs.map((pack) => pack.id) || []
}

async function upload(options: UploadRequestOptions) {
  const form = new FormData()
  form.append('file', options.file)
  const folderPath = normalizeFolderPath(selectedFolderPath.value)
  if (folderPath) form.append('folder_path', folderPath)
  if (selectedDomainId.value) form.append('domain_id', selectedDomainId.value)
  for (const packId of selectedPackIds.value) form.append('domain_pack_ids', packId)
  const response = await api.post<ParseTask>('/api/docs/upload', form)
  lastTask.value = response.data
  if (folderPath) currentFolder.value = folderPath
  ElMessage.info('文件已上传，后台开始解析')
  await loadAll()
  startPolling(response.data.id)
}

function startPolling(taskId: string) {
  activeTaskIds.value = Array.from(new Set([...activeTaskIds.value, taskId]))
  if (pollTimer) return
  pollTimer = window.setInterval(async () => {
    const ids = [...activeTaskIds.value]
    if (!ids.length) {
      if (pollTimer) window.clearInterval(pollTimer)
      pollTimer = undefined
      return
    }
    const responses = await Promise.all(ids.map((id) => api.get<ParseTask>(`/api/parse/status/${id}`)))
    await loadAll()
    for (const response of responses) {
      lastTask.value = response.data
      if (!['completed', 'failed', 'cancelled'].includes(response.data.status)) continue
      activeTaskIds.value = activeTaskIds.value.filter((id) => id !== response.data.id)
      ElMessage[response.data.status === 'completed' ? 'success' : response.data.status === 'cancelled' ? 'warning' : 'error'](response.data.message || response.data.status)
    }
    if (!activeTaskIds.value.length) {
      if (pollTimer) window.clearInterval(pollTimer)
      pollTimer = undefined
    }
  }, 2000)
}

async function cancelSource(row: SourceDocument) {
  await ElMessageBox.confirm(`停止“${row.filename}”的解析/LLM 抽取？已生成的临时数据会保留，可随后删除。`, '停止解析', {
    type: 'warning',
  })
  await api.post(`/api/sources/document/${row.id}/cancel`)
  ElMessage.warning('已请求停止解析')
  await loadAll()
}

async function deleteSource(row: SourceDocument) {
  await ElMessageBox.confirm(`删除“${row.filename}”及其 Wiki 节点、证据链和本地原始文件？`, '删除单个文档', {
    type: 'warning',
  })
  const response = await api.delete(`/api/sources/document/${row.id}`, { params: { delete_source_file: true } })
  const deleted = response.data.details || {}
  if (deleted.delete_pending) {
    ElMessage.warning('已请求停止并删除，后台任务会在下一个检查点完成清理')
    await loadAll()
    return
  }
  ElMessage.success(`已删除 ${deleted.wiki_nodes || 0} 个 Wiki 节点`)
  await loadAll()
}

async function reparseSource(row: SourceDocument) {
  await ElMessageBox.confirm(`使用当前 LLM 配置重新解析“${row.filename}”？旧 Wiki 节点和证据链会先被替换。`, '重新解析文档', {
    type: 'warning',
  })
  const response = await api.post<ParseTask>(`/api/sources/document/${row.id}/reparse`)
  lastTask.value = response.data
  ElMessage.info('已提交重新解析任务')
  await loadAll()
  startPolling(response.data.id)
}

async function clearKnowledge() {
  await ElMessageBox.confirm('确认清空当前库中的 Wiki、证据链、来源文档和解析任务？LLM 配置与领域包会保留。', '清空旧知识', {
    type: 'warning',
  })
  const response = await api.post('/api/admin/clear-knowledge', { delete_source_files: false })
  ElMessage.success(`已清空 ${response.data.deleted.wiki_nodes || 0} 个 Wiki 节点`)
  lastTask.value = null
  await loadAll()
}

function quality(row: SourceDocument) {
  return (row.document_metadata.quality || {}) as Record<string, unknown>
}

function llmStatus(row: SourceDocument) {
  const item = row.document_metadata.llm_extraction
  if (item && typeof item === 'object' && 'status' in item) return String((item as Record<string, unknown>).status || '')
  return ''
}

function needsOcr(row: SourceDocument) {
  return Boolean(quality(row).needs_ocr)
}

function qualityNote(row: SourceDocument) {
  const item = quality(row)
  const candidates = Array.isArray(item.ocr_candidates) ? item.ocr_candidates.join(', ') : ''
  if (item.needs_ocr && candidates) return ` · ${candidates}`
  if (typeof item.text_length === 'number') return ` · ${item.text_length} 字符`
  return ''
}

function sourceFolder(row: SourceDocument) {
  return normalizeFolderPath(String(row.document_metadata.folder_path || ''))
}

function sourceStatusLabel(row: SourceDocument) {
  if (row.status === 'delete_failed') return '长期记忆删除失败'
  if (llmStatus(row) === 'failed') return 'LLM失败/回退'
  return {
    uploaded: '已上传',
    parsing: '解析中',
    extracting: 'LLM 抽取中',
    cancel_requested: '停止中',
    deleting: '删除中',
    cancelled: '已停止',
    parsed: '已解析',
    parsed_with_warnings: '已解析/有警告',
    failed: '失败',
  }[row.status] || row.status
}

function sourceStatusType(row: SourceDocument) {
  if (llmStatus(row) === 'failed') return 'warning'
  if (row.status === 'delete_failed') return 'danger'
  if (row.status === 'parsed') return 'success'
  if (row.status === 'failed') return 'danger'
  if (row.status === 'deleting') return 'danger'
  if (row.status === 'cancelled') return 'info'
  if (row.status === 'extracting' || row.status === 'parsing' || row.status === 'cancel_requested' || row.status === 'parsed_with_warnings') return 'warning'
  return 'info'
}

function isProcessing(row: SourceDocument) {
  return ['uploaded', 'parsing', 'extracting'].includes(row.status)
}

function isDeleting(row: SourceDocument) {
  return row.status === 'deleting'
}

function normalizeFolderPath(value: string) {
  return value
    .replaceAll('\\', '/')
    .split('/')
    .map((part) => part.trim())
    .filter(Boolean)
    .join('/')
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

onMounted(loadAll)
onUnmounted(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>
