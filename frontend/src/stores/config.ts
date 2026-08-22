import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api, type LlmConfig, type LlmTestResponse, type WebSearchConfig, type WebSearchTestResponse } from '../api'

export const useConfigStore = defineStore('config', () => {
  const llmConfig = ref<LlmConfig | null>(null)
  const llmConfigs = ref<LlmConfig[]>([])
  const webSearchConfig = ref<WebSearchConfig | null>(null)
  const webSearchConfigs = ref<WebSearchConfig[]>([])
  const testResult = ref<LlmTestResponse | null>(null)
  const webSearchTestResult = ref<WebSearchTestResponse | null>(null)
  const loading = ref(false)

  async function fetchConfig() {
    const response = await api.get<LlmConfig>('/api/settings/llm-config')
    llmConfig.value = response.data
    return response.data
  }

  async function fetchConfigs() {
    const response = await api.get<LlmConfig[]>('/api/settings/llm-configs')
    llmConfigs.value = response.data
    llmConfig.value = response.data.find((item) => item.is_active) || llmConfig.value
    return response.data
  }

  async function fetchWebSearchConfig() {
    const response = await api.get<WebSearchConfig>('/api/settings/web-search-config')
    webSearchConfig.value = response.data
    return response.data
  }

  async function fetchWebSearchConfigs() {
    const response = await api.get<WebSearchConfig[]>('/api/settings/web-search-configs')
    webSearchConfigs.value = response.data
    webSearchConfig.value = response.data.find((item) => item.is_active) || webSearchConfig.value
    return response.data
  }

  async function saveConfig(config: LlmConfig) {
    loading.value = true
    try {
      const response = await api.put<LlmConfig>('/api/settings/llm-config', config)
      llmConfig.value = response.data
      await fetchConfigs()
      return response.data
    } finally {
      loading.value = false
    }
  }

  async function saveProfile(config: LlmConfig) {
    loading.value = true
    try {
      const request = config.id
        ? api.put<LlmConfig>(`/api/settings/llm-configs/${config.id}`, config)
        : api.post<LlmConfig>('/api/settings/llm-configs', config)
      const response = await request
      await fetchConfigs()
      return response.data
    } finally {
      loading.value = false
    }
  }

  async function activateProfile(id: number) {
    loading.value = true
    try {
      const response = await api.post<LlmConfig>(`/api/settings/llm-configs/${id}/activate`)
      llmConfig.value = response.data
      await fetchConfigs()
      return response.data
    } finally {
      loading.value = false
    }
  }

  async function deleteProfile(id: number) {
    await api.delete(`/api/settings/llm-configs/${id}`)
    await fetchConfigs()
  }

  async function saveWebSearchProfile(config: WebSearchConfig) {
    loading.value = true
    try {
      const request = config.id
        ? api.put<WebSearchConfig>(`/api/settings/web-search-configs/${config.id}`, config)
        : api.post<WebSearchConfig>('/api/settings/web-search-configs', config)
      const response = await request
      await fetchWebSearchConfigs()
      return response.data
    } finally {
      loading.value = false
    }
  }

  async function activateWebSearchProfile(id: number) {
    loading.value = true
    try {
      const response = await api.post<WebSearchConfig>(`/api/settings/web-search-configs/${id}/activate`)
      webSearchConfig.value = response.data
      await fetchWebSearchConfigs()
      return response.data
    } finally {
      loading.value = false
    }
  }

  async function deleteWebSearchProfile(id: number) {
    await api.delete(`/api/settings/web-search-configs/${id}`)
    await fetchWebSearchConfigs()
  }

  async function testConnection() {
    loading.value = true
    try {
      const response = await api.post<LlmTestResponse>('/api/settings/test-llm')
      testResult.value = response.data
      return response.data
    } finally {
      loading.value = false
    }
  }

  async function testWebSearch() {
    loading.value = true
    try {
      const active = webSearchConfigs.value.find((item) => item.is_active) || webSearchConfig.value
      const timeoutSeconds = Math.max(180, (active?.timeout_seconds || 45) * 2 + 30)
      const response = await api.post<WebSearchTestResponse>('/api/settings/test-web-search', undefined, {
        timeout: timeoutSeconds * 1000,
      })
      webSearchTestResult.value = response.data
      return response.data
    } finally {
      loading.value = false
    }
  }

  return {
    llmConfig,
    llmConfigs,
    webSearchConfig,
    webSearchConfigs,
    testResult,
    webSearchTestResult,
    loading,
    fetchConfig,
    fetchConfigs,
    fetchWebSearchConfig,
    fetchWebSearchConfigs,
    saveConfig,
    saveProfile,
    activateProfile,
    deleteProfile,
    saveWebSearchProfile,
    activateWebSearchProfile,
    deleteWebSearchProfile,
    testConnection,
    testWebSearch,
  }
})
