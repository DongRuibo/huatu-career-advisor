// 工作台的会话状态管理，负责新建会话、提交发言、刷新画像和刷新匹配。
// 维护请求状态和失败记录，支持手动修改偏好或确认建议后重新获取岗位。
// 保存画像版本与上次候选数，供界面展示结果是否过时及候选变化。

import { computed, ref } from 'vue'
import { api, messageFor } from './api'
import type { DialogueEntry, JobMatch, PreferenceUpdate, Profile, Session, Utterance } from './types'

// 创建咨询页的响应式状态与写入入口，集中协调会话切换、画像刷新和岗位读取。
export function useConsultation() {
  const session = ref<Session | null>(null)
  const profile = ref<Profile | null>(null)
  // null 表示尚未取得结果，空数组表示后端已完成匹配但没有候选。
  const matches = ref<JobMatch[] | null>(null)
  const entries = ref<DialogueEntry[]>([])
  // 画像版本用于标记已生成的追问和报告是否需要更新。
  const revision = ref(0)
  const previousCount = ref<number | null>(null)
  const refreshing = ref(false)
  const starting = ref(false)
  const submitting = ref(false)
  const pendingPreference = ref<string | null>(null)
  const sessionError = ref('')
  const profileError = ref('')
  const matchesError = ref('')
  // 画面正在切换会话、写入或刷新时，暂时停用会改变当前画像的操作。
  const locked = computed(() => starting.value || submitting.value || refreshing.value || pendingPreference.value !== null)
  let nextId = 0

  // 并行获取当前会话的画像与匹配结果，分别处理各接口失败并保留上次数据。
  async function refresh() {
    const id = session.value?.session_id
    if (!id || refreshing.value) return
    refreshing.value = true
    profileError.value = ''
    matchesError.value = ''
    try {
      // 分别处理两次读取，一个接口失败时保留该区域上次成功的数据。
      const [newProfile, newMatches] = await Promise.allSettled([api.getProfile(id), api.getMatches(id)])
      // 只接收当前会话的响应，防止旧请求覆盖其他会话的内容。
      if (session.value?.session_id !== id) return
      if (newProfile.status === 'fulfilled') profile.value = newProfile.value
      else profileError.value = messageFor(newProfile.reason)
      if (newMatches.status === 'fulfilled') {
        const oldCount = matches.value?.length ?? null
        if (oldCount !== newMatches.value.length) previousCount.value = oldCount
        matches.value = newMatches.value
      } else {
        matchesError.value = messageFor(newMatches.reason)
      }
    } finally {
      refreshing.value = false
    }
  }

  // 新会话创建成功后重置时间线和匹配视图，再读取该会话的初始结果。
  async function start() {
    if (locked.value) return
    starting.value = true
    sessionError.value = ''
    try {
      const created = await api.createSession()
      // 确认新会话创建成功后，才清除当前页面的数据。
      session.value = created
      entries.value = []
      profile.value = null
      matches.value = null
      previousCount.value = null
      revision.value = 0
      await refresh()
    } catch (error) {
      sessionError.value = messageFor(error)
    } finally {
      starting.value = false
    }
  }

  // 提交本轮发言并标记时间线结果，成功后刷新画像和候选，失败时返回 false 供表单保留草稿。
  async function submit(turn: Utterance): Promise<boolean> {
    const id = session.value?.session_id
    if (!id || locked.value) return false
    const entry = { ...turn, id: ++nextId, time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }
    const index = entries.value.length
    entries.value.push({ ...entry, state: 'pending' })
    submitting.value = true
    try {
      const result = await api.submitTurn(id, turn)
      entries.value[index] = { ...entry, state: 'success', result }
      if (result.changes.length || result.pending_suggestions.length || Object.keys(result.qualifications).length) revision.value++
      await refresh()
      return true
    } catch (error) {
      // 将失败留在对话时间线中。返回失败后输入框仍保留草稿供重试。
      entries.value[index] = { ...entry, state: 'failure', error: messageFor(error) }
      return false
    } finally {
      submitting.value = false
    }
  }

  // 执行手动设置或建议确认，使用后端保存的画像更新页面并重新读取候选。
  async function update(tagId: string, update: PreferenceUpdate) {
    const id = session.value?.session_id
    if (!id || locked.value) return false
    pendingPreference.value = tagId
    try {
      profile.value = await api.updatePreference(id, tagId, update)
      revision.value++
      // 手动修改和确认建议共用更新流程，保存后按新画像重新取匹配结果。
      await refresh()
      return true
    } finally {
      pendingPreference.value = null
    }
  }

  return {
    session, profile, matches, entries, revision, previousCount, refreshing, starting, submitting,
    pendingPreference, sessionError, profileError, matchesError, locked, refresh, start, submit, update,
  }
}
