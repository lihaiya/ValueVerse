<template>
  <section class="account-center">
    <section class="section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Account</p>
          <h1>用户中心</h1>
          <p class="muted">管理登录邮箱和账号密码。</p>
        </div>
        <el-tag :type="profile?.smtp_configured ? 'success' : 'warning'">
          {{ profile?.smtp_configured ? '系统邮件已配置' : '系统邮件未配置' }}
        </el-tag>
      </div>

      <div v-if="profile" class="account-identity">
        <span class="account-avatar">{{ profile.user.email.slice(0, 1).toUpperCase() }}</span>
        <div>
          <strong>{{ profile.user.email }}</strong>
          <span>注册于 {{ formatDate(profile.user.created_at) }}</span>
        </div>
      </div>

      <el-alert
        v-if="profile && !profile.smtp_configured"
        class="account-alert"
        type="warning"
        show-icon
        :closable="false"
        title="修改邮箱前需要配置系统发件邮箱"
        description="请在 Docker Compose 的环境变量中配置腾讯企业邮箱 SMTP 参数，然后重启 backend 容器。"
      />
    </section>

    <section class="section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Email</p>
          <h2>修改绑定邮箱</h2>
          <p class="muted">验证码会发送到新邮箱，验证通过后才会替换当前绑定邮箱。</p>
        </div>
      </div>

      <el-form ref="emailFormRef" class="account-form" :model="emailForm" :rules="emailRules" label-position="top">
        <el-form-item label="新邮箱" prop="new_email">
          <el-input v-model.trim="emailForm.new_email" autocomplete="email" placeholder="research@example.com" />
        </el-form-item>
        <el-form-item label="当前密码" prop="current_password">
          <el-input v-model="emailForm.current_password" type="password" autocomplete="current-password" show-password />
        </el-form-item>
        <el-form-item label="验证码" prop="code">
          <div class="account-code-row">
            <el-input v-model.trim="emailForm.code" inputmode="numeric" maxlength="6" placeholder="输入 6 位验证码" />
            <el-button :disabled="!profile?.smtp_configured || countdown > 0" :loading="requestingCode" @click="requestCode">
              {{ countdown > 0 ? `${countdown}s 后重发` : '发送验证码' }}
            </el-button>
          </div>
        </el-form-item>
        <div class="toolbar">
          <el-button type="primary" :loading="confirmingEmail" :disabled="!codeRequested" @click="confirmEmail">确认修改邮箱</el-button>
        </div>
      </el-form>
    </section>

    <section class="section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Password</p>
          <h2>修改登录密码</h2>
          <p class="muted">修改后当前登录会话继续有效，新密码用于下一次登录。</p>
        </div>
      </div>

      <el-form ref="passwordFormRef" class="account-form" :model="passwordForm" :rules="passwordRules" label-position="top">
        <el-form-item label="当前密码" prop="current_password">
          <el-input v-model="passwordForm.current_password" type="password" autocomplete="current-password" show-password />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input v-model="passwordForm.new_password" type="password" autocomplete="new-password" show-password />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm_password">
          <el-input v-model="passwordForm.confirm_password" type="password" autocomplete="new-password" show-password />
        </el-form-item>
        <div class="toolbar">
          <el-button type="primary" :loading="changingPassword" @click="changePassword">保存新密码</el-button>
        </div>
      </el-form>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { api, type AccountProfile, type AuthSession } from '../api'
import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const profile = ref<AccountProfile | null>(null)
const emailFormRef = ref<FormInstance>()
const passwordFormRef = ref<FormInstance>()
const requestingCode = ref(false)
const confirmingEmail = ref(false)
const changingPassword = ref(false)
const codeRequested = ref(false)
const countdown = ref(0)
let countdownTimer: number | undefined

const emailForm = reactive({ new_email: '', current_password: '', code: '' })
const passwordForm = reactive({ current_password: '', new_password: '', confirm_password: '' })

const emailRules: FormRules = {
  new_email: [{ required: true, type: 'email', message: '请输入有效邮箱', trigger: 'blur' }],
  current_password: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  code: [{ required: true, len: 6, message: '请输入 6 位验证码', trigger: 'blur' }],
}

const passwordRules: FormRules = {
  current_password: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  new_password: [
    { required: true, min: 8, message: '新密码至少 8 位', trigger: 'blur' },
    { validator: (_rule, value, callback) => (value === passwordForm.current_password ? callback(new Error('新密码不能与当前密码相同')) : callback()), trigger: 'blur' },
  ],
  confirm_password: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    { validator: (_rule, value, callback) => (value === passwordForm.new_password ? callback() : callback(new Error('两次输入的密码不一致'))), trigger: 'blur' },
  ],
}

async function loadProfile() {
  const response = await api.get<AccountProfile>('/api/account/profile')
  profile.value = response.data
}

async function requestCode() {
  await emailFormRef.value?.validateField(['new_email', 'current_password'])
  requestingCode.value = true
  try {
    await api.post('/api/account/email/request', {
      new_email: emailForm.new_email,
      current_password: emailForm.current_password,
    })
    codeRequested.value = true
    startCountdown()
    ElMessage.success('验证码已发送，请检查新邮箱')
  } finally {
    requestingCode.value = false
  }
}

async function confirmEmail() {
  await emailFormRef.value?.validate()
  confirmingEmail.value = true
  try {
    const response = await api.post<AuthSession>('/api/account/email/confirm', {
      new_email: emailForm.new_email,
      code: emailForm.code,
    })
    authStore.applySession(response.data)
    await loadProfile()
    Object.assign(emailForm, { new_email: '', current_password: '', code: '' })
    codeRequested.value = false
    ElMessage.success('绑定邮箱已更新')
  } finally {
    confirmingEmail.value = false
  }
}

async function changePassword() {
  await passwordFormRef.value?.validate()
  changingPassword.value = true
  try {
    const response = await api.post<AuthSession>('/api/account/password', passwordForm)
    authStore.applySession(response.data)
    Object.assign(passwordForm, { current_password: '', new_password: '', confirm_password: '' })
    ElMessage.success('登录密码已更新')
  } finally {
    changingPassword.value = false
  }
}

function startCountdown() {
  window.clearInterval(countdownTimer)
  countdown.value = 60
  countdownTimer = window.setInterval(() => {
    countdown.value -= 1
    if (countdown.value <= 0) window.clearInterval(countdownTimer)
  }, 1000)
}

function formatDate(value: string) {
  return new Date(value).toLocaleDateString('zh-CN')
}

onMounted(() => loadProfile().catch(() => ElMessage.error('无法加载账号信息')))
onBeforeUnmount(() => window.clearInterval(countdownTimer))
</script>
