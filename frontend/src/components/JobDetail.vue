<!--
岗位详情抽屉，把硬条件、软偏好、匹配理由和资格差距组织为可读明细。
点击生成讲解后展示正文、缺失资料与回退状态，各段引用可单独展开核对。
-->
<script setup lang="ts">
import { BriefcaseBusiness, ChevronRight, LoaderCircle, MapPin, Sparkles, X } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { api, messageFor } from '../api'
import { displayValue, qualificationStatus, scoreText, statusLabels, tagName } from '../presentation'
import type { JobExplanation, JobMatch } from '../types'

const props = defineProps<{ job: JobMatch; sessionId: string }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const tab = ref<'match' | 'explain'>('match')
const explanation = ref<JobExplanation | null>(null)
const loading = ref(false)
const error = ref('')
// 把后端讲解拆成概况、职责、工作方式和资格四段供模板循环展示。
const sections = computed(() => explanation.value ? [
  { title: '岗位概况', data: explanation.value.overview },
  { title: '主要职责', data: explanation.value.responsibilities },
  { title: '工作方式', data: explanation.value.work_style },
  { title: '资格准备', data: explanation.value.qualifications },
] : [])
// 按硬条件和软偏好组织岗位评估明细。
const assessments = computed(() => [
  { title: '硬条件', items: props.job.hard_conditions },
  { title: '软偏好评分明细', items: props.job.soft_scores },
])

// 组件挂载并获得原生弹窗节点后打开岗位详情。
onMounted(() => dialog.value?.showModal())

// 为选中的岗位请求讲解，独立管理加载状态和错误提示。
async function explain() {
  // 匹配明细随岗位数据打开，生成讲解需用户单独触发。
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    explanation.value = await api.explainJob(props.sessionId, props.job.job_id)
  } catch (reason) {
    error.value = messageFor(reason)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <dialog ref="dialog" class="offscreen-dialog detail-dialog" aria-labelledby="job-title" @cancel.prevent="emit('close')">
    <div class="detail-sheet">
      <header class="dialog-header">
        <div class="detail-identity">
          <span class="section-icon"><BriefcaseBusiness :size="19" /></span>
          <div><h2 id="job-title">{{ job.name || job.job_id }}</h2><p><MapPin :size="14" />{{ job.job_id }} · {{ job.city ?? '城市未知' }} · {{ job.direction ?? '方向未知' }}</p></div>
        </div>
        <button class="icon-button" type="button" aria-label="关闭岗位详情" @click="emit('close')"><X :size="21" /></button>
      </header>
      <nav class="detail-tabs segmented" aria-label="岗位详情视图">
        <button type="button" :aria-pressed="tab === 'match'" @click="tab = 'match'">匹配依据</button>
        <button type="button" :aria-pressed="tab === 'explain'" @click="tab = 'explain'">岗位讲解</button>
      </nav>

      <div v-if="tab === 'match'" class="detail-content">
        <div class="detail-metrics"><strong>{{ scoreText(job.score) }}</strong><div><span :class="['pill', job.hard_condition_status === 'confirmed' ? 'green' : 'amber']">{{ statusLabels[job.hard_condition_status] }} · {{ job.hard_condition_status }}</span><p>软偏好已评分 {{ job.scoring_coverage.scored_preferences }} 项，未知 {{ job.scoring_coverage.unknown_preferences }} 项</p></div></div>
        <section class="detail-section">
          <h3>主要匹配理由</h3>
          <ul v-if="job.match_reasons.length" class="detail-list"><li v-for="item in job.match_reasons" :key="item.tag_id"><strong>{{ item.tag_name }}</strong> · {{ item.detail }}</li></ul>
          <p v-else class="muted">当前暂无已确认的匹配理由。</p>
        </section>
        <section v-for="group in assessments" :key="group.title" class="detail-section">
          <h3>{{ group.title }}</h3>
          <p v-if="!group.items.length" class="muted">当前没有此类评估记录。</p>
          <article v-for="item in group.items" :key="item.tag_id" class="assessment">
            <div class="assessment-title"><strong>{{ item.tag_name }}</strong><span :class="['pill', item.status === 'unmet' || item.status === 'pending_confirmation' ? 'amber' : item.status === 'partially_matched' ? '' : 'green']">{{ statusLabels[item.status] ?? item.status }}</span><span v-if="item.score !== null" class="subcount">{{ scoreText(item.score) }}</span></div>
            <p>用户偏好 {{ displayValue(item.preference_value) }} · 岗位值 {{ item.job_value ?? '未知' }}</p>
            <p>{{ item.detail }}</p>
            <blockquote v-if="item.evidence">{{ item.evidence }}</blockquote>
          </article>
        </section>
        <section class="detail-section">
          <h3>未满足项与未知信息</h3>
          <ul v-if="job.unmet_preferences.length || job.unknown_tags.length" class="detail-list">
            <li v-for="item in job.unmet_preferences" :key="item.tag_id"><strong>{{ item.tag_name }}</strong> · {{ item.detail }}</li>
            <li v-if="job.unknown_tags.length">岗位资料未知 · {{ job.unknown_tags.map(tagName).join('、') }}</li>
          </ul>
          <p v-else class="muted">当前结果没有记录未满足项或未知标签。</p>
        </section>
        <section class="detail-section">
          <h3>资格提示与差距</h3>
          <p v-if="!job.qualification_hints.length" class="muted">暂无资格参考。</p>
          <article v-for="item in job.qualification_hints" :key="item.tag_id" class="assessment">
            <div class="assessment-title"><strong>{{ tagName(item.tag_id) }}</strong><span class="pill">{{ qualificationStatus[item.status] ?? item.status }}</span></div>
            <p>岗位参考 {{ item.requirement ?? '未提供' }} · 当前情况 {{ item.current ?? '未提供' }}</p>
            <p>{{ item.detail }}</p>
          </article>
        </section>
      </div>

      <div v-else class="detail-content" :aria-busy="loading">
        <div class="explain-toolbar">
          <div><h3>AI 岗位讲解</h3><p>{{ explanation ? explanation.generated_by === 'llm' ? '基于岗位资料生成' : '根据岗位资料整理' : '查看岗位职责、工作方式、资格与引用资料。' }}</p></div>
          <button class="button button-primary" type="button" :disabled="loading" @click="explain"><LoaderCircle v-if="loading" class="spin" :size="16" /><Sparkles v-else :size="16" />{{ loading ? '正在生成' : explanation ? '重新生成' : '生成岗位讲解' }}</button>
        </div>
        <p v-if="error" class="notice notice-error" role="alert">{{ error }}</p>
        <section v-for="section in sections" :key="section.title" class="detail-section">
          <h3>{{ section.title }}</h3>
          <p>{{ section.data.text }}</p>
          <details v-if="section.data.evidence.length" class="source-details">
            <summary class="source-toggle">
              <ChevronRight class="source-chevron" :size="14" aria-hidden="true" />
              <span class="source-expand">查看来源</span>
              <span class="source-collapse">收起来源</span>
              <span class="source-count">{{ section.data.evidence.length }} 条</span>
            </summary>
            <div v-for="source in section.data.evidence" :key="source.source_id + source.quote" class="source-quote">
              <strong>{{ source.source_id }}</strong>
              <blockquote>{{ source.quote }}</blockquote>
            </div>
          </details>
        </section>
        <section v-if="explanation?.missing_information.length" class="detail-section">
          <h3>资料未提供</h3><ul class="detail-list"><li v-for="item in explanation.missing_information" :key="item">{{ item }}</li></ul>
        </section>
      </div>
    </div>
  </dialog>
</template>
