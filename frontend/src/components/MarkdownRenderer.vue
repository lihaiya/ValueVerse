<template>
  <article class="markdown-body" v-html="html" @click="onClick" />
</template>

<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { computed } from 'vue'

const props = defineProps<{
  content: string
  linkMap?: Record<string, string>
}>()

const emit = defineEmits<{ 'missing-link': [title: string] }>()
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const defaultHeadingOpen = md.renderer.rules.heading_open || ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))
const defaultLinkOpen = md.renderer.rules.link_open || ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))

md.renderer.rules.heading_open = (tokens, idx, options, env, self) => {
  const inline = tokens[idx + 1]
  if (inline?.type === 'inline') {
    tokens[idx].attrSet('id', headingId(inline.content, env.headingIndex || 0))
    env.headingIndex = (env.headingIndex || 0) + 1
  }
  return defaultHeadingOpen(tokens, idx, options, env, self)
}

md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  const href = token.attrGet('href') || ''
  if (href.startsWith('/wiki/')) {
    token.attrSet('class', appendClass(token.attrGet('class'), 'wikilink'))
    token.attrSet('target', '_blank')
    token.attrSet('rel', 'noopener')
    token.attrSet('data-wiki-id', href.replace('/wiki/', ''))
  } else if (href.startsWith('#missing-wiki-')) {
    token.attrSet('class', appendClass(token.attrGet('class'), 'wikilink missing'))
    token.attrSet('data-missing-title', href.replace('#missing-wiki-', ''))
    token.attrSet('href', '#')
  }
  return defaultLinkOpen(tokens, idx, options, env, self)
}

const html = computed(() => {
  const prepared = props.content.replace(/\[\[([^\]]+)]]/g, (_, rawTitle: string) => {
    const title = rawTitle.trim()
    const id = props.linkMap?.[title]
    const encoded = encodeURIComponent(title)
    if (id) {
      return `[${escapeMarkdown(`[[${title}]]`)}](/wiki/${id})`
    }
    return `[${escapeMarkdown(`[[${title}]] 待创建`)}](#missing-wiki-${encoded})`
  })
  return md.render(prepared, { headingIndex: 0 })
})

function onClick(event: MouseEvent) {
  const target = event.target as HTMLElement
  const link = target.closest<HTMLElement>('[data-wiki-id]')
  if (link?.dataset.wikiId) {
    event.preventDefault()
    window.open(`/wiki/${link.dataset.wikiId}`, '_blank', 'noopener')
    return
  }
  const missing = target.closest<HTMLElement>('[data-missing-title]')
  if (missing?.dataset.missingTitle) {
    emit('missing-link', decodeURIComponent(missing.dataset.missingTitle))
  }
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => {
    const map: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }
    return map[char]
  })
}

function escapeMarkdown(value: string) {
  return value.replace(/([\\`*_[\]()#+\-.!])/g, '\\$1')
}

function headingId(text: string, index: number) {
  const clean = text.replace(/\[\[|]]/g, '').trim().toLowerCase()
  const slug = clean
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s+/g, '-')
    .slice(0, 80)
  return `heading-${index}-${slug || 'section'}`
}

function appendClass(current: string | null, next: string) {
  return [current, next].filter(Boolean).join(' ')
}
</script>
