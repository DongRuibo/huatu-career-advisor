<!--
工作台根组件，切换咨询与岗位管理，并保留已打开页面的状态。
从 useConsultation 读取共享状态，把对话、画像、岗位和报告分别交给对应组件。
收到编辑或岗位选择事件时打开弹窗，切换会话时清理旧会话的弹窗引用。
-->
<script setup lang="ts">
import { ArrowUpRight, BriefcaseBusiness, Compass, LoaderCircle, MessageCircle, Plus, ScanLine } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import ConversationPanel from './components/ConversationPanel.vue'
import JobDetail from './components/JobDetail.vue'
import JobManagement from './components/JobManagement.vue'
import PreferenceEditor from './components/PreferenceEditor.vue'
import ProfilePanel from './components/ProfilePanel.vue'
import Recommendations from './components/Recommendations.vue'
import ReportPanel from './components/ReportPanel.vue'
import { useConsultation } from './useConsultation'

const swaggerUrl = __SWAGGER_URL__
const view = ref<'consultation' | 'jobs'>('consultation')
const openedJobs = ref(false)
const jobsChanged = ref(false)
const {
  session, profile, matches, entries, revision, previousCount, refreshing, starting,
  sessionError, profileError, matchesError, locked, refresh, start, submit, update,
} = useConsultation()
// 读取当前会话 ID，子组件使用它请求同一份画像、岗位数据和规划报告。
const sessionId = computed(() => session.value?.session_id)
const editor = ref<{ tagId?: string } | null>(null)
const jobId = ref('')
// 详情按当前候选数据读取，匹配刷新后展示该岗位最新的证据和评分。
const selected = computed(() => matches.value?.find(/* 按选中的岗位 ID 查找最新详情 */ (item) => item.job_id === jobId.value))

// 会话切换时清除旧的编辑表单和岗位详情选择。
watch(sessionId, () => {
  editor.value = null
  jobId.value = ''
})

// 切换页面时保留会话与筛选，返回咨询页时同步已修改的岗位资料。
function openView(next: typeof view.value) {
  view.value = next
  if (next === 'jobs') openedJobs.value = true
  else if (jobsChanged.value) {
    jobsChanged.value = false
    if (sessionId.value) {
      revision.value++
      void refresh()
    }
  }
}
</script>

<template>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand"><span class="brand-symbol"><Compass :size="24" :stroke-width="1.7" /></span><strong>职业规划<span>工作台</span></strong></div>
      <nav class="workspace-nav" aria-label="工作区">
        <button class="button button-plain" type="button" :aria-pressed="view === 'consultation'" @click="openView('consultation')">
          <MessageCircle :size="15" />咨询工作台
        </button>
        <button class="button button-plain" type="button" :aria-pressed="view === 'jobs'" :disabled="locked" @click="openView('jobs')">
          <BriefcaseBusiness :size="15" />岗位管理
        </button>
      </nav>
      <nav class="top-actions" aria-label="工作台操作">
        <a class="button button-plain swagger-link" :href="swaggerUrl" target="_blank" rel="noopener noreferrer">Swagger 接口 <ArrowUpRight :size="15" /></a>
        <button v-if="view === 'consultation'" class="button button-primary new-session" type="button" :disabled="locked" @click="start">
          <LoaderCircle v-if="starting" class="spin" :size="17" /><Plus v-else :size="17" />{{ starting ? '正在创建' : '新建会话' }}
        </button>
      </nav>
    </div>
  </header>

  <main class="page">
    <JobManagement v-if="openedJobs" v-show="view === 'jobs'" @changed="jobsChanged = true" />
    <div v-show="view === 'consultation'">
      <div class="workspace-intro">
        <div>
          <h1>AI 职业规划咨询</h1>
          <p>记录多轮发言，梳理职业偏好，形成有依据的岗位推荐与规划报告。</p>
        </div>
        <div class="session-indicator" :class="{ 'session-active': sessionId }" aria-live="polite">
          <ScanLine :size="17" />
          <div><strong>{{ sessionId ? '咨询进行中' : '尚未创建会话' }}</strong><small v-if="sessionId" :title="sessionId">会话 {{ sessionId }}</small></div>
        </div>
      </div>
      <p v-if="sessionError" class="notice notice-error session-error" role="alert">创建会话失败 · {{ sessionError }}</p>
      <div class="workbench">
        <aside class="dialogue-column" aria-label="咨询对话区">
          <ConversationPanel :session-id="sessionId" :entries="entries" :locked="locked" :submit="submit" />
        </aside>
        <div class="results-column">
          <ProfilePanel :session-id="sessionId" :profile="profile" :loading="refreshing" :locked="locked" :error="profileError" :update="update" @edit="/* 打开选中标签的偏好编辑表单 */ (tagId) => { editor = { tagId } }" @refresh="refresh" />
          <Recommendations :key="sessionId || 'no-session'" :session-id="sessionId" :matches="matches" :previous-count="previousCount" :loading="refreshing" :locked="locked" :error="matchesError" :revision="revision" @select="jobId = $event" @refresh="refresh" />
          <ReportPanel :key="sessionId || 'no-session'" :session-id="sessionId" :revision="revision" :locked="locked" />
        </div>
      </div>
      <Teleport to="body">
        <PreferenceEditor v-if="editor" :tag-id="editor.tagId" :preferences="profile?.preferences ?? {}" :update="update" @close="editor = null" />
        <JobDetail v-if="selected && sessionId" :key="selected.job_id" :job="selected" :session-id="sessionId" @close="jobId = ''" />
      </Teleport>
    </div>
  </main>
</template>

<style scoped>
.workspace-nav { display: flex; align-items: center; gap: 4px; margin-right: auto; }
.workspace-nav button[aria-pressed=true] { background: var(--accent-tint); color: var(--accent-dark); }
@media (max-width: 755px) {
  .topbar-inner { height: auto; flex-wrap: wrap; gap: 10px; padding-top: 10px; padding-bottom: 8px; }
  .workspace-nav { order: 3; width: 100%; }
  .top-actions { margin-left: auto; }
}
</style>
