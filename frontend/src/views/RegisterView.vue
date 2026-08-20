<template>
  <section class="auth-page">
    <div class="auth-copy">
      <p class="eyebrow">Private Workspace</p>
      <h1>创建研究账号</h1>
      <p>一期注册后即可登录。系统会为你创建默认 Workspace，并初始化本地 Ollama 与 MiniMax 配置。</p>
    </div>

    <el-form class="auth-card" label-position="top" @submit.prevent="submit">
      <h2>邮箱注册</h2>
      <el-form-item label="邮箱">
        <el-input v-model="email" autocomplete="email" placeholder="research@example.com" />
      </el-form-item>
      <el-form-item label="密码">
        <el-input v-model="password" type="password" autocomplete="new-password" show-password />
      </el-form-item>
      <el-form-item label="确认密码">
        <el-input v-model="confirmPassword" type="password" autocomplete="new-password" show-password @keyup.enter="submit" />
      </el-form-item>
      <el-alert v-if="error" class="auth-error" type="error" :title="error" show-icon :closable="false" />
      <el-button class="auth-submit" type="primary" :loading="authStore.loading" @click="submit">注册并进入</el-button>
      <p class="auth-switch">已有账号？<RouterLink to="/auth/login">登录</RouterLink></p>
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
const confirmPassword = ref('')
const error = ref('')

async function submit() {
  error.value = ''
  if (password.value !== confirmPassword.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  try {
    await authStore.register(email.value, password.value)
    router.push('/dashboard')
  } catch (exc) {
    error.value = '注册失败，请检查邮箱是否已注册，密码至少 8 位'
  }
}
</script>
