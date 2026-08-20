import { computed, ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'
import { api, type WikiNode, type WikiNodeListItem } from '../api'

export const useWikiStore = defineStore('wiki', () => {
  const nodes = ref<WikiNodeListItem[]>([])
  const currentNode = ref<WikiNode | null>(null)
  const currentPath = ref<string[]>([])
  const linkCache = shallowRef(new Map<string, string>())
  const loading = ref(false)

  const titleMap = computed(() => {
    const map: Record<string, string> = {}
    for (const node of nodes.value) {
      map[node.title] = node.id
      for (const alias of node.aliases || []) {
        if (alias && !map[alias]) map[alias] = node.id
      }
    }
    return map
  })

  async function fetchNodes(q?: string) {
    loading.value = true
    try {
      const response = await api.get<WikiNodeListItem[]>('/api/wiki/nodes', { params: { q: q || undefined, limit: 1000 } })
      nodes.value = response.data
      const entries: [string, string][] = []
      for (const node of response.data) {
        entries.push([node.title, node.id])
        for (const alias of node.aliases || []) entries.push([alias, node.id])
      }
      linkCache.value = new Map(entries)
    } finally {
      loading.value = false
    }
  }

  async function fetchNode(id: string) {
    loading.value = true
    try {
      const response = await api.get<WikiNode>(`/api/wiki/node/${id}`)
      currentNode.value = response.data
      currentPath.value = buildPath(response.data)
      linkCache.value.set(response.data.title, response.data.id)
      return response.data
    } finally {
      loading.value = false
    }
  }

  async function resolveLink(title: string) {
    if (!nodes.value.length) await fetchNodes()
    return linkCache.value.get(title)
  }

  function buildPath(node: WikiNode) {
    const ticker = String(node.yaml_meta?.ticker || '').trim()
    const year = String(node.yaml_meta?.report_year || '').trim()
    return ['Wiki', ticker, year, node.title].filter(Boolean)
  }

  return { nodes, currentNode, currentPath, linkCache, titleMap, loading, fetchNodes, fetchNode, resolveLink }
})
