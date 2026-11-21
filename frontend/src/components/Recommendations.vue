<script setup lang="ts">
import { ArrowRight, BriefcaseBusiness, ChevronRight, LoaderCircle, MapPin, MessageCircleQuestion } from '@lucide/vue'
import { ref } from 'vue'
import { api, messageFor } from '../api'
import { scoreText } from '../presentation'
import type { FollowUpQuestion, JobMatch } from '../types'

const props = defineProps<{
  sessionId?: string
  matches: JobMatch[] | null
  previousCount: number | null
  loading: boolean
  locked: boolean
  error: string
  revision: number
}>()
const emit = defineEmits<{ select: [jobId: string]; refresh: [] }>()
const questions = ref<FollowUpQuestion[] | null>(null)
const questionRevision = ref(-1)
const asking = ref(false)
const questionError = ref('')

async function ask() {
  if (!props.sessionId || props.locked || asking.value) return
  const revision = props.revision
  asking.value = true
  questionError.value = ''
  try {
    questions.value = await api.getQuestions(props.sessionId)
    questionRevision.value = revision
  } catch (error) {
    questionError.value = messageFor(error)
  } finally {
    asking.value = false
  }
}
</script>

<template>
  <section class="panel" aria-labelledby="matches-title" :aria-busy="loading">
    <header class="panel-header">
      <span class="section-icon"><BriefcaseBusiness :size="19" /></span>
      <div><h2 id="matches-title">岗位智能推荐</h2><p>按硬条件筛选，根据已知软偏好评分</p></div>
      <div v-if="matches" class="match-total header-extra">
        <span v-if="previousCount !== null && previousCount !== matches.length" class="previous-count" aria-label="上次候选数量">{{ previousCount }} <ArrowRight :size="13" /></span>
        <strong>{{ matches.length }}</strong><span> 个候选</span>
      </div>
    </header>
    <div class="panel-body match-body">
      <p v-if="error" class="notice notice-error" role="alert">岗位读取失败 · {{ error }} <button class="button button-plain" type="button" :disabled="locked" @click="emit('refresh')">重试</button></p>
      <div v-if="!matches?.length" class="empty match-empty">
        <LoaderCircle v-if="loading" class="spin" :size="25" /><BriefcaseBusiness v-else :size="26" :stroke-width="1.6" />
        <h3>{{ loading ? '正在查询候选岗位' : matches ? '暂无候选岗位' : '候选岗位将在这里展示' }}</h3>
        <p>{{ matches && !loading ? '可以查看画像中的硬条件，按咨询内容调整后再试。' : !loading ? '创建咨询会话后查看岗位，再随画像变化更新。' : '' }}</p>
      </div>
      <ul v-else class="job-list" aria-label="匹配岗位">
        <li v-for="job in matches" :key="job.job_id">
          <button class="job-row" type="button" :data-job-id="job.job_id" @click="emit('select', job.job_id)">
            <span class="job-identity">
              <span class="job-title"><strong>{{ job.name || job.job_id }}</strong><span>{{ job.job_id }}</span></span>
              <span class="job-location"><MapPin :size="13" />{{ job.city ?? '城市未知' }}<span class="inline-divider" />{{ job.direction ?? '方向未知' }}</span>
            </span>
            <span class="job-coverage"><span :class="['pill', job.hard_condition_status === 'confirmed' ? 'green' : 'amber']" :title="job.hard_condition_status">{{ job.hard_condition_status === 'confirmed' ? '硬条件已确认' : '硬条件待确认' }}</span><small>已评分 {{ job.scoring_coverage.scored_preferences }} · 未知 {{ job.scoring_coverage.unknown_preferences }}</small></span>
            <span class="job-score"><strong>{{ scoreText(job.score) }}</strong><small v-if="job.score !== null">软偏好分</small></span>
            <ChevronRight class="row-chevron" :size="16" />
          </button>
        </li>
      </ul>
      <div class="question-box">
        <div class="question-header">
          <MessageCircleQuestion :size="19" />
          <div><h3>智能追问</h3><p>针对尚未生效、能区分候选岗位的偏好</p></div>
          <button class="button button-secondary" type="button" :disabled="!sessionId || locked || asking" @click="ask">
            <LoaderCircle v-if="asking" class="spin" :size="15" />{{ asking ? '正在生成' : '生成追问' }}
          </button>
        </div>
        <p v-if="questionError" class="notice notice-error" role="alert">{{ questionError }}</p>
        <p v-if="questions && questionRevision !== revision" class="muted question-note">画像已更新，可重新生成本轮追问。</p>
        <p v-if="questions && !questions.length" class="muted question-note">当前候选中暂无足以区分岗位的未明确偏好。</p>
        <ol v-if="questions?.length" class="question-list">
          <li v-for="question in questions" :key="question.tag_id"><strong>{{ question.text }}</strong><small>{{ question.tag_name }} · {{ question.known_jobs }}/{{ question.candidate_jobs }} 个候选有资料</small></li>
        </ol>
      </div>
    </div>
  </section>
</template>
