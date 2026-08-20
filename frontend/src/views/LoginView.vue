<template>
  <section class="auth-page">
    <div class="auth-copy">
      <p class="eyebrow">LLM Wiki Workbench</p>
      <h1>登录研究工作台</h1>
      <p>每个账号拥有独立 Workspace，年报、Wiki、图谱、模型配置和对话记录互相隔离。</p>
    </div>

    <el-form class="auth-card" label-position="top" @submit.prevent="submit">
      <h2>邮箱登录</h2>
      <el-form-item label="邮箱">
        <el-input v-model="email" autocomplete="email" placeholder="research@example.com" />
      </el-form-item>
      <el-form-item label="密码">
        <el-input v-model="password" type="password" autocomplete="current-password" show-password @keyup.enter="submit" />
      </el-form-item>
      <el-checkbox v-model="remember">保持登录</el-checkbox>
      <el-alert v-if="error" class="auth-error" type="error" :title="error" show-icon :closable="false" />
      <el-button class="auth-submit" type="primary" :loading="authStore.loading" @click="submit">登录</el-button>
      <p class="auth-switch">还没有账号？<RouterLink to="/auth/register">注册</RouterLink></p>
    </el-form>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const email = ref('')
const password = ref('')
const remember = ref(true)
const error = ref('')

async function submit() {
  error.value = ''
  try {
    await authStore.login(email.value, password.value, remember.value)
    router.push('/dashboard')
  } catch (exc) {
    error.value = '邮箱或密码不正确'
  }
}
</script>
