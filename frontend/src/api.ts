import axios from 'axios'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 30000,
  withCredentials: true,
})

api.interceptors.request.use((config) => {
  const workspaceId = localStorage.getItem('valueverse_active_workspace_id')
  if (workspaceId) {
    config.headers = config.headers || {}
    config.headers['X-Workspace-Id'] = workspaceId
  }
  return config
})

export interface WikiNodeListItem {
  id: string
  title: string
  type: string
  aliases?: string[]
  analysis_status?: string
  credibility_score?: number
  cognee_doc_hash?: string
  updated_at: string
}

export interface User {
  id: string
  email: string
  is_active: boolean
  is_superuser: boolean
  is_verified: boolean
  created_at: string
  last_login_at?: string
}

export interface Workspace {
  id: string
  name: string
  description?: string
  role: string
  active: boolean
  created_at: string
}

export interface AuthSession {
  user: User
  workspaces: Workspace[]
  active_workspace?: Workspace
}

export interface AccountProfile {
  user: User
  smtp_configured: boolean
}

export interface WikiNode extends WikiNodeListItem {
  yaml_meta: Record<string, unknown>
  content_md?: string
  raw_content_ref?: string
  created_at: string
  related_nodes: Array<Record<string, unknown>>
}

export interface RawContent {
  node_id: string
  filename: string
  kind: 'text' | 'pdf'
  mime_type: string
  text?: string
  base64?: string
}

export interface SourceDocument {
  id: string
  filename: string
  mime_type?: string
  storage_backend: string
  storage_uri: string
  sha256: string
  size_bytes: number
  status: string
  document_metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface SourceSpan {
  id: string
  source_document_id: string
  parsed_artifact_id?: string
  span_type: string
  locator: Record<string, unknown>
  text: string
  char_start?: number
  char_end?: number
  confidence: number
  created_at: string
}

export interface EvidenceItem {
  id: string
  target_type: string
  target_id: string
  source_span_id: string
  quote?: string
  relevance_score: number
  evidence_metadata: Record<string, unknown>
  created_at: string
  span: SourceSpan
}

export interface DomainPack {
  id: string
  slug: string
  name: string
  description?: string
  owner_type: string
  version: string
  is_active: boolean
  config: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface Domain {
  id: string
  slug: string
  name: string
  description?: string
  owner_type: string
  is_active: boolean
  created_at: string
  updated_at: string
  domain_packs: DomainPack[]
}

export interface DomainPayload {
  slug: string
  name: string
  description?: string
  owner_type?: string
  domain_pack_ids: string[]
}

export interface DomainPackPayload {
  slug: string
  name: string
  description?: string
  owner_type?: string
  version?: string
  is_active?: boolean
  config: Record<string, unknown>
}

export interface LlmConfig {
  id?: number
  profile_name: string
  provider: string
  endpoint: string
  model_name: string
  api_key?: string
  has_api_key?: boolean
  api_key_masked?: string
  temperature: number
  max_tokens: number
  is_active?: boolean
  updated_by?: string
  updated_at?: string
}

export interface WebSearchConfig {
  id?: number
  profile_name: string
  provider: string
  endpoint: string
  api_key?: string
  has_api_key?: boolean
  api_key_masked?: string
  command: string
  args: string[]
  tool_name: string
  timeout_seconds: number
  max_results: number
  is_active?: boolean
  updated_by?: string
  updated_at?: string
}

export interface Citation {
  node_id?: string
  title: string
  score: number
  link: string
}

export interface RecallResponse {
  answer: string
  citations: Citation[]
  confidence: number
  memory_backend: string
  conversation_id?: string
}

export interface ChatMessageRecord {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  citations: Array<Record<string, unknown>>
  confidence?: number
  memory_backend?: string
  created_at: string
}

export interface ChatConversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  messages: ChatMessageRecord[]
}

export interface LlmTestResponse {
  ok: boolean
  provider: string
  endpoint: string
  model_name: string
  latency_ms: number
  message: string
}

export interface WebSearchResult {
  title: string
  url?: string
  snippet: string
  raw: Record<string, unknown>
}

export interface WebSearchTestResponse {
  ok: boolean
  provider: string
  endpoint: string
  latency_ms: number
  message: string
  results: WebSearchResult[]
}

export interface GraphNode {
  id: string
  label: string
  type: string
  ticker?: string
  company_name?: string
  company_short_name?: string
  report_year?: number
  folder_path?: string
  status?: string
  updated_at?: string
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  relation_type: string
  weight: number
  metadata: Record<string, unknown>
}

export interface GraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
}
