import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api, type AuthSession, type Workspace } from '../api'

export const useAuthStore = defineStore('auth', () => {
  const session = ref<AuthSession | null>(null)
  const loading = ref(false)
  const initialized = ref(false)

  const user = computed(() => session.value?.user || null)
  const workspaces = computed(() => session.value?.workspaces || [])
  const activeWorkspace = computed(() => session.value?.active_workspace || workspaces.value[0] || null)
  const isAuthenticated = computed(() => Boolean(user.value))

  function applySession(next: AuthSession | null) {
    session.value = next
    const active = next?.active_workspace || next?.workspaces?.[0]
    if (active?.id) {
      localStorage.setItem('valueverse_active_workspace_id', active.id)
    } else {
      localStorage.removeItem('valueverse_active_workspace_id')
    }
  }

  async function fetchMe() {
    loading.value = true
    try {
      const response = await api.get<AuthSession>('/api/auth/me')
      applySession(response.data)
      return response.data
    } finally {
      loading.value = false
      initialized.value = true
    }
  }

  async function login(email: string, password: string, remember = true) {
    loading.value = true
    try {
      const response = await api.post<AuthSession>('/api/auth/login', { email, password, remember })
      applySession(response.data)
      initialized.value = true
      return response.data
    } finally {
      loading.value = false
    }
  }

  async function register(email: string, password: string) {
    loading.value = true
    try {
      const response = await api.post<AuthSession>('/api/auth/register', {
        email,
        password,
      })
      applySession(response.data)
      initialized.value = true
      return response.data
    } finally {
      loading.value = false
    }
  }

  async function logout() {
    await api.post('/api/auth/logout')
    applySession(null)
  }

  function switchWorkspace(workspace: Workspace) {
    localStorage.setItem('valueverse_active_workspace_id', workspace.id)
    if (!session.value) return
    session.value = {
      ...session.value,
      active_workspace: { ...workspace, active: true },
      workspaces: session.value.workspaces.map((item) => ({ ...item, active: item.id === workspace.id })),
    }
  }

  return {
    session,
    user,
    workspaces,
    activeWorkspace,
    isAuthenticated,
    loading,
    initialized,
    applySession,
    fetchMe,
    login,
    register,
    logout,
    switchWorkspace,
  }
})
