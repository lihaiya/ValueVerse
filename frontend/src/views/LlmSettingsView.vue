<template>
  <section class="llm-settings-grid">
    <aside class="section llm-profile-list">
      <div class="section-header">
        <div>
          <p class="eyebrow">LLM Profiles</p>
          <h2>模型配置档案</h2>
        </div>
        <el-button :icon="Plus" type="primary" plain @click="newProfile">新建</el-button>
      </div>

      <div class="profile-list">
        <button
          v-for="profile in configStore.llmConfigs"
          :key="profile.id"
          class="profile-row"
          :class="{ active: profile.id === form.id }"
          type="button"
          @click="selectProfile(profile)"
        >
          <span>
            <strong>{{ profile.profile_name }}</strong>
            <small>{{ providerLabel(profile.provider) }} · {{ profile.model_name }}</small>
          </span>
          <el-tag v-if="profile.is_active" size="small" type="success">当前</el-tag>
        </button>
      </div>
    </aside>

    <div class="settings-stack">
    <section class="section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Runtime</p>
          <h2>动态模型配置</h2>
          <p class="muted">保存多个配置档案后，可直接切换本地模型或外部供应商，不需要反复输入。</p>
        </div>
        <el-tag v-if="savedAt" type="success">已生效 {{ savedAt }}</el-tag>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-width="120px" class="llm-form">
        <el-form-item label="配置名称" prop="profile_name">
          <el-input v-model.trim="form.profile_name" placeholder="本地 qwen / MiniMax M3 / 供应商1" />
        </el-form-item>
        <el-form-item label="Provider" prop="provider">
          <el-radio-group v-model="form.provider" @change="applyProviderPreset">
            <el-radio value="ollama">Ollama 本地</el-radio>
            <el-radio value="minimax">MiniMax</el-radio>
            <el-radio value="openai">OpenAI API</el-radio>
            <el-radio value="custom_api">自定义 Endpoint</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="Endpoint" prop="endpoint">
          <el-input v-model.trim="form.endpoint" placeholder="http://localhost:11434" />
        </el-form-item>
        <el-form-item label="模型名称" prop="model_name">
          <el-input v-model.trim="form.model_name" placeholder="qwen3.6:27b / MiniMax-M3" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            autocomplete="off"
            :placeholder="apiKeyPlaceholder"
          />
          <div class="form-help">{{ apiKeyHelp }}</div>
        </el-form-item>
        <el-form-item label="Temperature" prop="temperature">
          <el-slider v-model="form.temperature" :min="0" :max="2" :step="0.1" show-input :marks="{ 0: '保守', 1: '平衡', 2: '发散' }" />
        </el-form-item>
        <el-form-item label="上下文 / Token" prop="max_tokens">
          <el-input-number v-model="form.max_tokens" :min="512" :max="262144" :step="1024" />
        </el-form-item>
        <el-form-item>
          <div class="toolbar">
            <el-button :icon="Link" :loading="testing" @click="testConnection">测试已激活配置</el-button>
            <el-button :icon="Check" :loading="saving" @click="saveOnly">保存档案</el-button>
            <el-button type="primary" :icon="SwitchButton" :loading="saving" @click="saveAndActivate">保存并激活</el-button>
            <el-button v-if="form.id && !form.is_active" type="danger" plain :icon="Delete" @click="deleteProfile">删除</el-button>
          </div>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="testResult"
        class="test-result"
        :type="testResult.ok ? 'success' : 'error'"
        :title="testResult.ok ? `连接成功 ${testResult.latency_ms}ms` : '连接失败'"
        :description="`${testResult.endpoint} · ${testResult.model_name} · ${testResult.message}`"
        show-icon
        :closable="false"
      />
    </section>
    <section class="section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Web Search</p>
          <h2>联网搜索配置</h2>
          <p class="muted">当前接入 MiniMax Token Plan MCP 的 web_search 工具；问答页开启联网搜索后才会调用。</p>
        </div>
        <el-tag v-if="activeWebSearch" type="success">{{ activeWebSearch.profile_name }}</el-tag>
      </div>

      <div class="profile-list web-profile-list">
        <button
          v-for="profile in configStore.webSearchConfigs"
          :key="profile.id"
          class="profile-row"
          :class="{ active: profile.id === webForm.id }"
          type="button"
          @click="selectWebSearchProfile(profile)"
        >
          <span>
            <strong>{{ profile.profile_name }}</strong>
            <small>{{ webSearchProviderLabel(profile.provider) }} · {{ profile.tool_name }}</small>
          </span>
          <el-tag v-if="profile.is_active" size="small" type="success">当前</el-tag>
        </button>
      </div>

      <el-form ref="webFormRef" :model="webForm" :rules="webRules" label-width="120px" class="llm-form">
        <el-form-item label="配置名称" prop="profile_name">
          <el-input v-model.trim="webForm.profile_name" placeholder="MiniMax Web Search" />
        </el-form-item>
        <el-form-item label="Provider" prop="provider">
          <el-radio-group v-model="webForm.provider" @change="applyWebSearchPreset">
            <el-radio value="minimax_mcp">MiniMax MCP</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="API Host" prop="endpoint">
          <el-input v-model.trim="webForm.endpoint" placeholder="https://api.minimaxi.com" />
        </el-form-item>
        <el-form-item label="Token Plan Key">
          <el-input
            v-model="webForm.api_key"
            type="password"
            show-password
            autocomplete="off"
            :placeholder="webApiKeyPlaceholder"
          />
          <div class="form-help">MiniMax 文档说明 Token Plan Key 与普通按量 API Key 不互通；留空表示保留已保存 Key。</div>
        </el-form-item>
        <el-form-item label="MCP 命令" prop="command">
          <el-input v-model.trim="webForm.command" />
        </el-form-item>
        <el-form-item label="MCP 参数">
          <el-input v-model="webArgsText" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="Tool Name" prop="tool_name">
          <el-input v-model.trim="webForm.tool_name" />
        </el-form-item>
        <el-form-item label="结果数量" prop="max_results">
          <el-input-number v-model="webForm.max_results" :min="1" :max="10" />
        </el-form-item>
        <el-form-item label="超时秒数" prop="timeout_seconds">
          <el-input-number v-model="webForm.timeout_seconds" :min="5" :max="180" />
        </el-form-item>
        <el-form-item>
          <div class="toolbar">
            <el-button :icon="Link" :loading="testingWebSearch" @click="testWebSearch">测试已激活搜索</el-button>
            <el-button :icon="Check" :loading="savingWebSearch" @click="saveWebSearchOnly">保存档案</el-button>
            <el-button type="primary" :icon="SwitchButton" :loading="savingWebSearch" @click="saveAndActivateWebSearch">保存并激活</el-button>
            <el-button v-if="webForm.id && !webForm.is_active" type="danger" plain :icon="Delete" @click="deleteWebSearchProfile">删除</el-button>
          </div>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="webSearchTestResult"
        class="test-result"
        :type="webSearchTestResult.ok ? 'success' : 'error'"
        :title="webSearchTestResult.ok ? `搜索可用 ${webSearchTestResult.latency_ms}ms` : '搜索不可用'"
        :description="`${webSearchTestResult.endpoint} · ${webSearchTestResult.message}`"
        show-icon
        :closable="false"
      />
    </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Delete, Link, Plus, SwitchButton } from '@element-plus/icons-vue'
import type { LlmConfig, LlmTestResponse, WebSearchConfig, WebSearchTestResponse } from '../api'
import { useConfigStore } from '../stores/config'

const configStore = useConfigStore()
const formRef = ref<FormInstance>()
const saving = ref(false)
const testing = ref(false)
const savedAt = ref('')
const testResult = ref<LlmTestResponse | null>(null)
const webSearchTestResult = ref<WebSearchTestResponse | null>(null)
const webFormRef = ref<FormInstance>()
const savingWebSearch = ref(false)
const testingWebSearch = ref(false)
const webForm = reactive<WebSearchForm>(blankWebSearchProfile())
const webArgsText = ref(webForm.args.join('\n'))

interface WebSearchForm extends WebSearchConfig {
  args_text?: string
}
const form = reactive<LlmConfig>(blankProfile())

const rules: FormRules = {
  profile_name: [{ required: true, message: '请输入配置名称', trigger: 'blur' }],
  provider: [{ required: true, message: '请选择 provider', trigger: 'change' }],
  endpoint: [{ required: true, message: '请输入 endpoint', trigger: 'blur' }],
  model_name: [{ required: true, message: '请输入模型名称', trigger: 'blur' }],
  temperature: [{ type: 'number', min: 0, max: 2, message: '范围 0-2', trigger: 'change' }],
  max_tokens: [{ type: 'number', min: 512, max: 262144, message: '范围 512-262144', trigger: 'change' }],
}

const webRules: FormRules = {
  profile_name: [{ required: true, message: '请输入配置名称', trigger: 'blur' }],
  provider: [{ required: true, message: '请选择 provider', trigger: 'change' }],
  endpoint: [{ required: true, message: '请输入 API Host', trigger: 'blur' }],
  command: [{ required: true, message: '请输入 MCP 命令', trigger: 'blur' }],
  tool_name: [{ required: true, message: '请输入 Tool Name', trigger: 'blur' }],
  max_results: [{ type: 'number', min: 1, max: 10, message: '范围 1-10', trigger: 'change' }],
  timeout_seconds: [{ type: 'number', min: 5, max: 180, message: '范围 5-180', trigger: 'change' }],
}

async function loadConfig() {
  await configStore.fetchConfigs()
  await configStore.fetchWebSearchConfigs()
  const active = configStore.llmConfigs.find((item) => item.is_active) || configStore.llmConfigs[0]
  if (active) selectProfile(active)
  const activeWeb = configStore.webSearchConfigs.find((item) => item.is_active) || configStore.webSearchConfigs[0]
  if (activeWeb) selectWebSearchProfile(activeWeb)
}

function selectProfile(profile: LlmConfig) {
  Object.assign(form, { ...profile, api_key: '' })
}

function newProfile() {
  Object.assign(form, blankProfile())
}

function selectWebSearchProfile(profile: WebSearchConfig) {
  Object.assign(webForm, { ...profile, api_key: '' })
  webArgsText.value = (profile.args || []).join('\n')
}

function applyWebSearchPreset() {
  webForm.profile_name = webForm.profile_name || 'MiniMax Token Plan Web Search'
  webForm.endpoint = 'https://api.minimaxi.com'
  webForm.command = 'uvx'
  webForm.tool_name = 'web_search'
  webForm.max_results = webForm.max_results || 5
  webForm.timeout_seconds = webForm.timeout_seconds || 45
  webArgsText.value = 'minimax-coding-plan-mcp\n-y'
}

function applyProviderPreset() {
  if (form.provider === 'ollama') {
    form.profile_name = form.profile_name || '本地 Ollama'
    form.endpoint = 'http://localhost:11434'
    form.model_name = form.model_name || 'qwen3.6:27b'
  } else if (form.provider === 'minimax') {
    form.profile_name = form.profile_name || 'MiniMax M3'
    form.endpoint = 'https://api.minimaxi.com/v1'
    form.model_name = 'MiniMax-M3'
  } else if (form.provider === 'openai') {
    form.endpoint = 'https://api.openai.com/v1'
    form.model_name = form.model_name || 'gpt-4.1'
  }
}

async function testConnection() {
  testing.value = true
  try {
    testResult.value = await configStore.testConnection()
  } finally {
    testing.value = false
  }
}

async function saveOnly() {
  await persist(false)
}

async function saveAndActivate() {
  await persist(true)
}

async function persist(activate: boolean) {
  await formRef.value?.validate()
  saving.value = true
  try {
    const payload = { ...form, is_active: activate || Boolean(form.is_active) }
    const config = await configStore.saveProfile(payload)
    if (activate && config.id) await configStore.activateProfile(config.id)
    const latest = configStore.llmConfigs.find((item) => item.id === config.id) || config
    selectProfile(latest)
    savedAt.value = new Date().toLocaleTimeString()
    ElMessage.success(activate ? '配置已保存并激活' : '配置已保存')
  } finally {
    saving.value = false
  }
}

async function deleteProfile() {
  if (!form.id) return
  await ElMessageBox.confirm(`确认删除配置「${form.profile_name}」？`, '删除配置', { type: 'warning' })
  await configStore.deleteProfile(form.id)
  ElMessage.success('配置已删除')
  newProfile()
}

async function testWebSearch() {
  testingWebSearch.value = true
  try {
    webSearchTestResult.value = await configStore.testWebSearch()
  } finally {
    testingWebSearch.value = false
  }
}

async function saveWebSearchOnly() {
  await persistWebSearch(false)
}

async function saveAndActivateWebSearch() {
  await persistWebSearch(true)
}

async function persistWebSearch(activate: boolean) {
  await webFormRef.value?.validate()
  savingWebSearch.value = true
  try {
    const payload = { ...webForm, args: parseWebArgs(), is_active: activate || Boolean(webForm.is_active) }
    const config = await configStore.saveWebSearchProfile(payload)
    if (activate && config.id) await configStore.activateWebSearchProfile(config.id)
    const latest = configStore.webSearchConfigs.find((item) => item.id === config.id) || config
    selectWebSearchProfile(latest)
    ElMessage.success(activate ? '联网搜索配置已保存并激活' : '联网搜索配置已保存')
  } finally {
    savingWebSearch.value = false
  }
}

async function deleteWebSearchProfile() {
  if (!webForm.id) return
  await ElMessageBox.confirm(`确认删除配置「${webForm.profile_name}」？`, '删除配置', { type: 'warning' })
  await configStore.deleteWebSearchProfile(webForm.id)
  ElMessage.success('配置已删除')
  Object.assign(webForm, blankWebSearchProfile())
  webArgsText.value = webForm.args.join('\n')
}

function providerLabel(provider: string) {
  return { ollama: 'Ollama', minimax: 'MiniMax', openai: 'OpenAI', custom_api: '自定义' }[provider] || provider
}

function webSearchProviderLabel(provider: string) {
  return { minimax_mcp: 'MiniMax Token Plan MCP' }[provider] || provider
}

const activeWebSearch = computed(() => configStore.webSearchConfigs.find((item) => item.is_active))

const apiKeyPlaceholder = computed(() => {
  if (form.has_api_key) return `已保存 ${form.api_key_masked}；留空则保持不变`
  if (form.provider === 'ollama') return '可选：Ollama 网关或反向代理需要鉴权时填写'
  return '输入 API Key'
})

const apiKeyHelp = computed(() => {
  if (form.provider === 'ollama') return '本地 Ollama 默认不需要 Key；如果你前面接了鉴权代理，这里会作为 Bearer Token 发送。'
  return '保存时不会回显明文；切换档案后留空表示保留原 Key。'
})

const webApiKeyPlaceholder = computed(() => {
  if (webForm.has_api_key) return `已保存 ${webForm.api_key_masked}；留空则保持不变`
  return '输入 MiniMax Token Plan Key'
})

function parseWebArgs() {
  return webArgsText.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

function blankProfile(): LlmConfig {
  return {
    profile_name: '',
    provider: 'ollama',
    endpoint: 'http://localhost:11434',
    model_name: 'qwen3.6:27b',
    api_key: '',
    temperature: 0.2,
    max_tokens: 32768,
    is_active: false,
    updated_by: 'web',
  }
}

function blankWebSearchProfile(): WebSearchForm {
  return {
    profile_name: 'MiniMax Token Plan Web Search',
    provider: 'minimax_mcp',
    endpoint: 'https://api.minimaxi.com',
    api_key: '',
    command: 'uvx',
    args: ['minimax-coding-plan-mcp', '-y'],
    tool_name: 'web_search',
    timeout_seconds: 45,
    max_results: 5,
    is_active: false,
    updated_by: 'web',
  }
}

onMounted(loadConfig)
</script>
