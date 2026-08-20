import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api, type ChatConversation, type Citation, type RecallResponse } from '../api'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  confidence?: number
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const conversations = ref<ChatConversation[]>([])
  const activeConversationId = ref<string | null>(null)
  const isStreaming = ref(false)
  const citations = ref<Citation[]>([])
  const lastLatencyMs = ref<number | null>(null)

  async function sendQuery(text: string, options: { useWebSearch?: boolean } = {}) {
    const query = text.trim()
    if (!query) return
    messages.value.push({ role: 'user', content: query })
    isStreaming.value = true
    const started = performance.now()
    try {
      const response = await api.post<RecallResponse>('/api/agent/dialog', {
        query,
        top_k: 5,
        use_web_search: Boolean(options.useWebSearch),
        conversation_id: activeConversationId.value,
      })
      activeConversationId.value = response.data.conversation_id || activeConversationId.value
      lastLatencyMs.value = Math.round(performance.now() - started)
      citations.value = response.data.citations
      messages.value.push({
        role: 'assistant',
        content: response.data.answer,
        citations: response.data.citations,
        confidence: response.data.confidence,
      })
      await fetchConversations()
    } finally {
      isStreaming.value = false
    }
  }

  async function fetchConversations() {
    const response = await api.get<ChatConversation[]>('/api/chat/conversations')
    conversations.value = response.data
    return response.data
  }

  async function openConversation(id: string) {
    const response = await api.get<ChatConversation>(`/api/chat/conversations/${id}`)
    activeConversationId.value = response.data.id
    messages.value = response.data.messages.map((message) => ({
      role: message.role,
      content: message.content,
      citations: (message.citations || []) as unknown as Citation[],
      confidence: message.confidence,
    }))
    let latestCitations: Citation[] = []
    for (let index = messages.value.length - 1; index >= 0; index -= 1) {
      const message = messages.value[index]
      if (message.role === 'assistant' && message.citations?.length) {
        latestCitations = message.citations
        break
      }
    }
    citations.value = latestCitations
    return response.data
  }

  async function renameConversation(id: string, title: string) {
    await api.put(`/api/chat/conversations/${id}`, { title })
    await fetchConversations()
  }

  async function deleteConversation(id: string) {
    await api.delete(`/api/chat/conversations/${id}`)
    if (activeConversationId.value === id) clearSession()
    await fetchConversations()
  }

  function clearSession() {
    activeConversationId.value = null
    messages.value = []
    citations.value = []
  }

  return {
    messages,
    conversations,
    activeConversationId,
    isStreaming,
    citations,
    lastLatencyMs,
    sendQuery,
    fetchConversations,
    openConversation,
    renameConversation,
    deleteConversation,
    clearSession,
  }
})
