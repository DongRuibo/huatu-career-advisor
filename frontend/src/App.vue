<script setup lang="ts">
import { ArrowUpRight, Compass, LoaderCircle, Plus, ScanLine } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import ConversationPanel from './components/ConversationPanel.vue'
import JobDetail from './components/JobDetail.vue'
import PreferenceEditor from './components/PreferenceEditor.vue'
import ProfilePanel from './components/ProfilePanel.vue'
import Recommendations from './components/Recommendations.vue'
import ReportPanel from './components/ReportPanel.vue'
import { useConsultation } from './useConsultation'

const swaggerUrl = __SWAGGER_URL__
const {
  session, profile, matches, entries, revision, previousCount, refreshing, starting,
  sessionError, profileError, matchesError, locked, refresh, start, submit, update,
} = useConsultation()
const sessionId = computed(() => session.value?.session_id)
const editor = ref<{ tagId?: string } | null>(null)
const jobId = ref('')
const selected = computed(() => matches.value?.find((item) => item.job_id === jobId.value))

watch(sessionId, () => {
  editor.value = null
  jobId.value = ''
})
</script>

<template>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand"><span class="brand-symbol"><Compass :size="24" :stroke-width="1.7" /></span><strong>职业规划<span>工作台</span></strong></div>
      <nav class="top-actions" aria-label="工作台操作">
        <a class="button button-plain swagger-link" :href="swaggerUrl" target="_blank" rel="noopener noreferrer">Swagger 接口 <ArrowUpRight :size="15" /></a>
        <button class="button button-primary new-session" type="button" :disabled="locked" @click="start">
          <LoaderCircle v-if="starting" class="spin" :size="17" /><Plus v-else :size="17" />{{ starting ? '正在创建' : '新建会话' }}
        </button>
      </nav>
    </div>
  </header>

  <main class="page">
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
        <ProfilePanel :session-id="sessionId" :profile="profile" :loading="refreshing" :locked="locked" :error="profileError" :update="update" @edit="(tagId) => { editor = { tagId } }" @refresh="refresh" />
        <Recommendations :key="sessionId || 'no-session'" :session-id="sessionId" :matches="matches" :previous-count="previousCount" :loading="refreshing" :locked="locked" :error="matchesError" :revision="revision" @select="jobId = $event" @refresh="refresh" />
        <ReportPanel :key="sessionId || 'no-session'" :session-id="sessionId" :revision="revision" :locked="locked" />
      </div>
    </div>
    <Teleport to="body">
      <PreferenceEditor v-if="editor" :tag-id="editor.tagId" :preferences="profile?.preferences ?? {}" :update="update" @close="editor = null" />
      <JobDetail v-if="selected && sessionId" :key="selected.job_id" :job="selected" :session-id="sessionId" @close="jobId = ''" />
    </Teleport>
  </main>
</template>
