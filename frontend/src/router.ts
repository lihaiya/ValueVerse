import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from './stores/auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/auth/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
    { path: '/auth/register', name: 'register', component: () => import('./views/RegisterView.vue'), meta: { public: true } },
    { path: '/dashboard', name: 'dashboard', component: () => import('./views/DashboardView.vue') },
    { path: '/docs', name: 'docs', component: () => import('./views/DocManageView.vue') },
    { path: '/companies', name: 'companies', component: () => import('./views/CompanyListView.vue') },
    { path: '/wiki', name: 'wiki-list', component: () => import('./views/WikiListView.vue') },
    { path: '/wiki/:id', name: 'wiki-reader', component: () => import('./views/WikiReaderView.vue'), props: true },
    { path: '/company/:ticker', name: 'company-profile', component: () => import('./views/CompanyProfileView.vue'), props: true },
    { path: '/graph', name: 'graph', component: () => import('./views/GraphCanvasView.vue') },
    { path: '/chat', name: 'chat', component: () => import('./views/RagChatView.vue') },
    { path: '/memory', redirect: '/chat' },
    { path: '/settings/llm', name: 'llm-settings', component: () => import('./views/LlmSettingsView.vue') },
    { path: '/settings/domains', name: 'domain-settings', component: () => import('./views/DomainManageView.vue') },
    { path: '/settings/account', name: 'account-settings', component: () => import('./views/UserCenterView.vue') },
  ],
})

router.beforeEach(async (to) => {
  const authStore = useAuthStore()
  const isPublic = Boolean(to.meta.public)
  if (!authStore.initialized) {
    try {
      await authStore.fetchMe()
    } catch {
      authStore.initialized = true
    }
  }
  if (!authStore.isAuthenticated && !isPublic) {
    return { path: '/auth/login', query: { redirect: to.fullPath } }
  }
  if (authStore.isAuthenticated && isPublic) {
    return '/dashboard'
  }
  return true
})
