<template>
  <section class="chat-layout">
    <aside class="chat-history section">
      <div class="section-header compact">
        <div>
          <h2>对话</h2>
          <p class="muted">按 Workspace 保存</p>
        </div>
        <el-button size="small" @click="chatStore.clearSession">新建</el-button>
      </div>
      <div class="history-list">
        <button
          v-for="conversation in chatStore.conversations"
          :key="conversation.id"
          class="history-row"
          :class="{ active: conversation.id === chatStore.activeConversationId }"
          @click="chatStore.openConversation(conversation.id)"
        >
          <strong>{{ conversation.title }}</strong>
          <span>{{ formatTime(conversation.updated_at) }}</span>
        </button>
      </div>
    </aside>

    <main class="chat-stream section">
      <div class="section-header">
        <div>
          <h2>价值投资研究对话</h2>
          <p class="muted">基于当前 Workspace 的 Wiki、Cognee 记忆与可选联网搜索</p>
        </div>
        <div class="toolbar">
          <el-switch v-model="useWebSearch" active-text="联网搜索" />
          <el-button @click="chatStore.clearSession">清空当前</el-button>
        </div>
      </div>

      <div class="messages">
        <div v-for="(message, index) in chatStore.messages" :key="index" :class="['message', message.role]">
          <strong>{{ message.role === 'user' ? '研究员' : 'AI' }}</strong>
          <StreamingResponse :content="message.content" :streaming="message.role === 'assistant' && index === chatStore.messages.length - 1" :link-map="wikiStore.titleMap" />
          <div v-if="message.citations?.length" class="citation-row">
            <RouterLink v-for="citation in message.citations?.filter((item) => item.node_id)" :key="citation.link" :to="`/wiki/${citation.node_id}`">
              <el-tag>{{ citation.title }}</el-tag>
            </RouterLink>
            <a v-for="citation in message.citations?.filter((item) => !item.node_id)" :key="citation.link" :href="citation.link" target="_blank" rel="noopener">
              <el-tag type="info">{{ citation.title }}</el-tag>
            </a>
          </div>
        </div>
      </div>

      <div class="chat-input">
        <el-input v-model="query" type="textarea" :rows="3" placeholder="例如：介绍一下小商品城的商业模式和主要风险" @keyup.ctrl.enter="send" />
        <el-button type="primary" :loading="chatStore.isStreaming" @click="send">发送</el-button>
      </div>
    </main>

    <aside class="context-panel section">
      <h2>引用与上下文</h2>
      <el-tag v-if="configStore.llmConfig">{{ configStore.llmConfig.model_name }}</el-tag>
      <el-tag v-if="chatStore.lastLatencyMs" type="success">{{ chatStore.lastLatencyMs }}ms</el-tag>
      <el-divider />
      <RouterLink v-for="citation in chatStore.citations.filter((item) => item.node_id)" :key="citation.link" :to="`/wiki/${citation.node_id}`">
        <div class="citation-card">
          <strong>{{ citation.title }}</strong>
          <span>score {{ citation.score.toFixed(2) }}</span>
        </div>
      </RouterLink>
      <a v-for="citation in chatStore.citations.filter((item) => !item.node_id)" :key="citation.link" :href="citation.link" target="_blank" rel="noopener">
        <div class="citation-card">
          <strong>{{ citation.title }}</strong>
          <span>external</span>
        </div>
      </a>
    </aside>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import StreamingResponse from '../components/StreamingResponse.vue'
import { useChatStore } from '../stores/chat'
import { useConfigStore } from '../stores/config'
import { useWikiStore } from '../stores/wiki'

const chatStore = useChatStore()
const configStore = useConfigStore()
const wikiStore = useWikiStore()
const query = ref('')
const useWebSearch = ref(false)

async function send() {
  await chatStore.sendQuery(query.value, { useWebSearch: useWebSearch.value })
  query.value = ''
}

function formatTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

onMounted(() => {
  wikiStore.fetchNodes()
  configStore.fetchConfig()
  configStore.fetchWebSearchConfig()
  chatStore.fetchConversations()
})
</script>
