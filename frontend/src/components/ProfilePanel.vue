<!--
画像面板，展示当前硬条件、软偏好、原话依据、资格和待确认建议。
手动编辑由父组件打开表单，确认建议时携带原发言数据库 ID。
-->
<script setup lang="ts">
import { CirclePlus, LoaderCircle, Pencil, UserRound } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import { messageFor } from '../api'
import { displayValue, qualificationLabels, tagName } from '../presentation'
import type { PreferenceUpdate, Profile, Suggestion } from '../types'

const props = defineProps<{
  sessionId?: string
  profile: Profile | null
  loading: boolean
  locked: boolean
  error: string
  update: (tagId: string, update: PreferenceUpdate) => Promise<boolean>
}>()
const emit = defineEmits<{ edit: [tagId?: string]; refresh: [] }>()
// 从已生效偏好按强度生成硬条件和软偏好两组。
const groups = computed(() => {
  const active = Object.values(props.profile?.preferences ?? {})
  return [
    // 筛出用于过滤的硬条件。
    { id: 'hard', title: '硬条件', items: active.filter((item) => item.strength === 'hard') },
    // 筛出用于评分的软偏好。
    { id: 'soft', title: '软偏好', items: active.filter((item) => item.strength === 'soft') },
  ]
})
// 把后端按标签索引的待确认建议转换为可遍历的列表。
const suggestions = computed(() => Object.values(props.profile?.pending_suggestions ?? {}))
const confirmError = ref('')
const confirming = ref('')
watch(/* 观察当前会话 ID */ () => props.sessionId, /* 切换会话后清除旧的确认错误 */ () => { confirmError.value = '' })

// 携带建议对应的原话 ID 发起显式确认，并显示该建议的请求结果。
async function confirm(suggestion: Suggestion) {
  if (suggestion.evidence_turn_id === null || props.locked) return
  confirmError.value = ''
  confirming.value = suggestion.tag_id
  try {
    // 使用建议原有的证据 ID，后端据此核对会话和建议状态。
    await props.update(suggestion.tag_id, { action: 'confirm', evidence_turn_id: suggestion.evidence_turn_id })
  } catch (error) {
    confirmError.value = messageFor(error)
  } finally {
    confirming.value = ''
  }
}
</script>

<template>
  <section class="panel" aria-labelledby="profile-title" :aria-busy="loading">
    <header class="panel-header">
      <span class="section-icon"><UserRound :size="19" /></span>
      <div><h2 id="profile-title">用户职业画像</h2><p>查看生效偏好、原话依据及资格信息</p></div>
      <button class="button button-secondary header-extra" type="button" :disabled="!sessionId || locked" @click="emit('edit')"><CirclePlus :size="16" />添加偏好</button>
    </header>
    <div class="panel-body profile-body">
      <p v-if="error" class="notice notice-error" role="alert">画像读取失败 · {{ error }} <button class="button button-plain" type="button" :disabled="locked" @click="emit('refresh')">重试</button></p>
      <p v-if="loading && !profile" class="muted state-line" role="status"><LoaderCircle class="spin" :size="16" />正在读取画像</p>
      <div class="profile-grid">
        <div v-for="group in groups" :key="group.id" class="profile-group">
          <h3><span :class="['dot', group.id]" />{{ group.title }}<span class="subcount">{{ group.items.length }}</span></h3>
          <p v-if="!group.items.length" class="group-empty">暂无已识别的{{ group.title }}</p>
          <article v-for="item in group.items" :key="item.tag_id" class="preference-row">
            <div class="preference-line"><span>{{ tagName(item.tag_id) }}</span><strong>{{ displayValue(item.value, item.constraint_type) }}</strong><button class="icon-button" type="button" :aria-label="`修改${tagName(item.tag_id)}`" :disabled="locked" @click="emit('edit', item.tag_id)"><Pencil :size="14" /></button></div>
            <blockquote v-if="item.evidence_text">{{ item.evidence_text }}</blockquote>
            <p v-else class="muted evidence-empty">暂无关联原话</p>
          </article>
        </div>
      </div>
      <div class="qualification-strip">
        <h3>当前资格</h3>
        <dl>
          <div v-for="(label, key) in qualificationLabels" :key="key">
            <dt>{{ label }}</dt>
            <dd>{{ profile?.qualifications[key] === undefined ? '未提供' : `${profile.qualifications[key]}${key === 'experience_years' ? ' 年' : ''}` }}</dd>
          </div>
        </dl>
      </div>
      <div v-if="suggestions.length || confirmError" class="suggestions">
        <h3>待确认建议 <span class="pill amber">{{ suggestions.length }}</span></h3>
        <p v-if="confirmError" class="notice notice-error" role="alert">确认失败 · {{ confirmError }}</p>
        <article v-for="suggestion in suggestions" :key="suggestion.tag_id" class="suggestion-row">
          <div><strong>{{ tagName(suggestion.tag_id) }} · {{ displayValue(suggestion.value) }}</strong><blockquote v-if="suggestion.evidence_text">{{ suggestion.evidence_text }}</blockquote></div>
          <button type="button" class="button button-secondary" :disabled="locked || suggestion.evidence_turn_id === null" @click="confirm(suggestion)">{{ confirming === suggestion.tag_id ? '正在确认' : '确认生效' }}</button>
        </article>
      </div>
    </div>
  </section>
</template>
