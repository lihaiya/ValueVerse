<template>
  <section class="domain-workbench">
    <div class="domain-rail">
      <div class="rail-head">
        <div>
          <p class="eyebrow">Domain Registry</p>
          <h2>领域管理</h2>
        </div>
        <el-button :icon="Refresh" text :loading="loading" @click="loadAll">刷新</el-button>
      </div>

      <el-form class="domain-create" label-position="top">
        <el-form-item label="领域名称">
          <el-input v-model="domainForm.name" placeholder="A股价值投资" @blur="fillDomainSlug" />
        </el-form-item>
        <el-form-item label="领域标识">
          <el-input v-model="domainForm.slug" placeholder="a-share-value-investing" />
        </el-form-item>
        <el-form-item label="领域包">
          <el-select v-model="domainForm.domain_pack_ids" multiple collapse-tags collapse-tags-tooltip placeholder="选择领域包">
            <el-option v-for="pack in activePacks" :key="pack.id" :label="pack.name" :value="pack.id" />
          </el-select>
        </el-form-item>
        <el-button type="primary" :icon="Plus" :loading="savingDomain" @click="createDomain">创建领域</el-button>
      </el-form>

      <div class="domain-list">
        <button
          v-for="domain in domains"
          :key="domain.id"
          class="domain-row"
          :class="{ active: domain.id === selectedDomainId }"
          type="button"
          @click="selectDomain(domain.id)"
        >
          <span>
            <strong>{{ domain.name }}</strong>
            <small>{{ domain.slug }}</small>
          </span>
          <el-tag size="small" :type="domain.owner_type === 'system' ? 'info' : 'success'">
            {{ domain.domain_packs.length }} 包
          </el-tag>
        </button>
      </div>
    </div>

    <div class="domain-main">
      <section class="section domain-current" v-if="selectedDomain">
        <div class="section-header">
          <div>
            <p class="eyebrow">Active Domain</p>
            <h2>{{ selectedDomain.name }}</h2>
          </div>
          <div class="toolbar">
            <el-tag>{{ selectedDomain.owner_type }}</el-tag>
            <el-button :icon="Delete" text type="danger" @click="deleteDomain(selectedDomain)">删除领域</el-button>
          </div>
        </div>

        <el-form label-position="top">
          <el-form-item label="领域描述">
            <el-input v-model="selectedDescription" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item label="绑定领域包">
            <el-checkbox-group v-model="selectedPackIds" class="pack-checks">
              <el-checkbox v-for="pack in activePacks" :key="pack.id" :label="pack.id">
                <span>{{ pack.name }}</span>
                <small>{{ pack.slug }}@{{ pack.version }}</small>
              </el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <el-button type="primary" :icon="Check" :loading="savingDomain" @click="saveSelectedDomain">保存领域配置</el-button>
        </el-form>
      </section>

      <section class="section pack-catalog">
        <div class="section-header">
          <div>
            <p class="eyebrow">Domain Packs</p>
            <h2>领域包管理</h2>
          </div>
          <el-button type="primary" :icon="Plus" @click="openPackDialog()">新建领域包</el-button>
        </div>

        <el-table :data="packs" stripe>
          <el-table-column label="领域包" min-width="260">
            <template #default="{ row }">
              <div class="table-title">
                <strong>{{ row.name }}</strong>
                <span>{{ row.slug }}@{{ row.version }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="owner_type" label="来源" width="100" />
          <el-table-column label="规则摘要" min-width="260">
            <template #default="{ row }">
              <span class="muted">{{ summarizePack(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="160" fixed="right">
            <template #default="{ row }">
              <el-button size="small" text @click="openPackDialog(row)">编辑</el-button>
              <el-button size="small" text type="danger" @click="deletePack(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </div>

    <el-dialog v-model="packDialogVisible" :title="editingPack ? '编辑领域包' : '新建领域包'" width="720px">
      <el-form label-position="top">
        <div class="two-col">
          <el-form-item label="名称">
            <el-input v-model="packForm.name" />
          </el-form-item>
          <el-form-item label="标识">
            <el-input v-model="packForm.slug" :disabled="Boolean(editingPack)" @blur="fillPackSlug" />
          </el-form-item>
        </div>
        <div class="two-col">
          <el-form-item label="版本">
            <el-input v-model="packForm.version" :disabled="Boolean(editingPack)" />
          </el-form-item>
          <el-form-item label="状态">
            <el-switch v-model="packForm.is_active" active-text="启用" inactive-text="停用" />
          </el-form-item>
        </div>
        <el-form-item label="描述">
          <el-input v-model="packForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="规则 JSON">
          <el-input v-model="packConfigText" type="textarea" :rows="14" class="json-editor" spellcheck="false" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="packDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingPack" @click="savePack">保存领域包</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Check, Delete, Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, type Domain, type DomainPack, type DomainPackPayload, type DomainPayload } from '../api'

const domains = ref<Domain[]>([])
const packs = ref<DomainPack[]>([])
const loading = ref(false)
const savingDomain = ref(false)
const savingPack = ref(false)
const selectedDomainId = ref('')
const selectedPackIds = ref<string[]>([])
const selectedDescription = ref('')
const packDialogVisible = ref(false)
const editingPack = ref<DomainPack | null>(null)
const packConfigText = ref('')

const domainForm = reactive<DomainPayload>({
  slug: '',
  name: '',
  description: '',
  owner_type: 'user',
  domain_pack_ids: [],
})

const packForm = reactive<DomainPackPayload>({
  slug: '',
  name: '',
  description: '',
  owner_type: 'user',
  version: '1.0.0',
  is_active: true,
  config: {},
})

const activePacks = computed(() => packs.value.filter((pack) => pack.is_active))
const selectedDomain = computed(() => domains.value.find((domain) => domain.id === selectedDomainId.value))

async function loadAll() {
  loading.value = true
  try {
    const [domainResponse, packResponse] = await Promise.all([
      api.get<Domain[]>('/api/domains'),
      api.get<DomainPack[]>('/api/domain-packs', { params: { active_only: false } }),
    ])
    domains.value = domainResponse.data
    packs.value = packResponse.data
    if (!selectedDomainId.value && domains.value.length) selectDomain(domains.value[0].id)
    if (selectedDomainId.value && !domains.value.some((domain) => domain.id === selectedDomainId.value)) {
      selectDomain(domains.value[0]?.id || '')
    }
  } finally {
    loading.value = false
  }
}

function selectDomain(id: string) {
  selectedDomainId.value = id
  const domain = domains.value.find((item) => item.id === id)
  selectedPackIds.value = domain?.domain_packs.map((pack) => pack.id) || []
  selectedDescription.value = domain?.description || ''
}

function fillDomainSlug() {
  if (!domainForm.slug && domainForm.name) domainForm.slug = slugify(domainForm.name)
}

function fillPackSlug() {
  if (!packForm.slug && packForm.name) packForm.slug = slugify(packForm.name)
}

async function createDomain() {
  fillDomainSlug()
  if (!domainForm.name || !domainForm.slug) {
    ElMessage.warning('请填写领域名称和标识')
    return
  }
  savingDomain.value = true
  try {
    const response = await api.post<Domain>('/api/domains', domainForm)
    domains.value = [response.data, ...domains.value]
    selectDomain(response.data.id)
    Object.assign(domainForm, { slug: '', name: '', description: '', owner_type: 'user', domain_pack_ids: [] })
    ElMessage.success('领域已创建')
  } finally {
    savingDomain.value = false
  }
}

async function saveSelectedDomain() {
  if (!selectedDomain.value) return
  savingDomain.value = true
  try {
    const response = await api.put<Domain>(`/api/domains/${selectedDomain.value.id}`, {
      description: selectedDescription.value,
      domain_pack_ids: selectedPackIds.value,
    })
    domains.value = domains.value.map((domain) => (domain.id === response.data.id ? response.data : domain))
    selectDomain(response.data.id)
    ElMessage.success('领域配置已保存')
  } finally {
    savingDomain.value = false
  }
}

async function deleteDomain(domain: Domain) {
  await ElMessageBox.confirm(`确认删除领域「${domain.name}」？系统领域会被停用。`, '删除领域', { type: 'warning' })
  await api.delete(`/api/domains/${domain.id}`)
  ElMessage.success('领域已删除')
  await loadAll()
}

function openPackDialog(pack?: DomainPack) {
  editingPack.value = pack || null
  if (pack) {
    Object.assign(packForm, {
      slug: pack.slug,
      name: pack.name,
      description: pack.description || '',
      owner_type: pack.owner_type,
      version: pack.version,
      is_active: pack.is_active,
      config: pack.config,
    })
    packConfigText.value = JSON.stringify(pack.config, null, 2)
  } else {
    Object.assign(packForm, {
      slug: '',
      name: '',
      description: '',
      owner_type: 'user',
      version: '1.0.0',
      is_active: true,
      config: defaultPackConfig(),
    })
    packConfigText.value = JSON.stringify(defaultPackConfig(), null, 2)
  }
  packDialogVisible.value = true
}

async function savePack() {
  fillPackSlug()
  let config: Record<string, unknown>
  try {
    config = JSON.parse(packConfigText.value || '{}')
  } catch {
    ElMessage.error('规则 JSON 格式不正确')
    return
  }
  savingPack.value = true
  try {
    if (editingPack.value) {
      await api.put(`/api/domain-packs/${editingPack.value.id}`, {
        name: packForm.name,
        description: packForm.description,
        is_active: packForm.is_active,
        config,
      })
    } else {
      await api.post('/api/domain-packs', { ...packForm, config })
    }
    packDialogVisible.value = false
    ElMessage.success('领域包已保存')
    await loadAll()
  } finally {
    savingPack.value = false
  }
}

async function deletePack(pack: DomainPack) {
  await ElMessageBox.confirm(`确认删除领域包「${pack.name}」？系统领域包会被停用。`, '删除领域包', { type: 'warning' })
  await api.delete(`/api/domain-packs/${pack.id}`)
  ElMessage.success('领域包已删除')
  await loadAll()
}

function summarizePack(pack: DomainPack) {
  const nodeTypes = Array.isArray(pack.config.node_types) ? pack.config.node_types.length : 0
  const edgeTypes = Array.isArray(pack.config.edge_types) ? pack.config.edge_types.length : 0
  const routing = pack.config.routing && typeof pack.config.routing === 'object' ? '含路由规则' : '无路由规则'
  return `${nodeTypes} 类节点 / ${edgeTypes} 类边 / ${routing}`
}

function defaultPackConfig() {
  return {
    routing: { keywords: [] },
    node_types: [],
    edge_types: [],
    extraction_targets: [],
    evidence: { required_for: [], prefer: ['paragraph'] },
  }
}

function slugify(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

onMounted(loadAll)
</script>
