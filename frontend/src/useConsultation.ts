import { computed, ref } from 'vue'
import { api, messageFor } from './api'
import type { DialogueEntry, JobMatch, PreferenceUpdate, Profile, Session, Utterance } from './types'

export function useConsultation() {
  const session = ref<Session | null>(null)
  const profile = ref<Profile | null>(null)
  const matches = ref<JobMatch[] | null>(null)
  const entries = ref<DialogueEntry[]>([])
  const revision = ref(0)
  const previousCount = ref<number | null>(null)
  const refreshing = ref(false)
  const starting = ref(false)
  const submitting = ref(false)
  const pendingPreference = ref<string | null>(null)
  const sessionError = ref('')
  const profileError = ref('')
  const matchesError = ref('')
  const locked = computed(() => starting.value || submitting.value || refreshing.value || pendingPreference.value !== null)
  let nextId = 0

  async function refresh() {
    const id = session.value?.session_id
    if (!id || refreshing.value) return
    refreshing.value = true
    profileError.value = ''
    matchesError.value = ''
    try {
      const [newProfile, newMatches] = await Promise.allSettled([api.getProfile(id), api.getMatches(id)])
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

  async function start() {
    if (locked.value) return
    starting.value = true
    sessionError.value = ''
    try {
      const created = await api.createSession()
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
      entries.value[index] = { ...entry, state: 'failure', error: messageFor(error) }
      return false
    } finally {
      submitting.value = false
    }
  }

  async function update(tagId: string, update: PreferenceUpdate) {
    const id = session.value?.session_id
    if (!id || locked.value) return false
    pendingPreference.value = tagId
    try {
      profile.value = await api.updatePreference(id, tagId, update)
      revision.value++
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
