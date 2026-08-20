<template>
  <aside class="wiki-tree">
    <el-input v-model="filter" clearable size="small" placeholder="过滤目录/链接" />
    <el-scrollbar class="tree-scroll">
      <el-tree
        :data="treeData"
        :props="{ label: 'label', children: 'children' }"
        default-expand-all
        node-key="id"
        @node-click="openNode"
      />
    </el-scrollbar>
  </aside>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { WikiNode } from '../api'

const props = defineProps<{
  node: WikiNode
  linkMap: Record<string, string>
}>()
const emit = defineEmits<{ 'jump-heading': [id: string] }>()

const router = useRouter()
const filter = ref('')

const treeData = computed(() => {
  const headings = Array.from((props.node.content_md || '').matchAll(/^(#{1,3})\s+(.+)$/gm)).map((match, index) => ({
    id: headingId(match[2], index),
    label: match[2].replace(/\[\[|]]/g, ''),
    type: 'heading',
  }))
  const relatedMeta = Array.isArray(props.node.yaml_meta.related) ? props.node.yaml_meta.related.map(String) : []
  const related = Array.from(new Set([...relatedMeta, ...extractLinks(props.node.content_md || '')])).map((title) => ({
    id: props.linkMap[title] || `missing-${title}`,
    label: title,
    type: 'wiki',
  }))
  const data = [
    { id: 'contents', label: '目录', children: headings },
    { id: 'related', label: '双向链接', children: related },
  ]
  if (!filter.value.trim()) return data
  const keyword = filter.value.trim()
  return data.map((group) => ({ ...group, children: group.children.filter((item) => item.label.includes(keyword)) }))
})

function openNode(data: any) {
  if (data.type === 'heading') {
    emit('jump-heading', data.id)
    return
  }
  if (data.type === 'wiki' && props.linkMap[data.label]) {
    const target = router.resolve(`/wiki/${props.linkMap[data.label]}`).href
    window.open(target, '_blank', 'noopener')
  }
}

function extractLinks(markdown: string) {
  return Array.from(markdown.matchAll(/\[\[([^\]]+)]]/g)).map((match) => match[1].trim())
}

function headingId(text: string, index: number) {
  const clean = text.replace(/\[\[|]]/g, '').trim().toLowerCase()
  const slug = clean
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s+/g, '-')
    .slice(0, 80)
  return `heading-${index}-${slug || 'section'}`
}
</script>
