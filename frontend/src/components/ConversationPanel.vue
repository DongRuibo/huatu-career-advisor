<script setup lang="ts">
import { ArrowDownToLine, ArrowUp, Check, LoaderCircle, MessageCircle, TriangleAlert } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import { displayValue, tagName } from '../presentation'
import { scenarios } from '../scenarios'
import type { DialogueEntry, Speaker, Utterance } from '../types'

const props = defineProps<{
  sessionId?: string
  entries: DialogueEntry[]
  locked: boolean
  submit: (turn: Utterance) => Promise<boolean>
}>()
const draft = ref('')
const speaker = ref<Speaker>('家长')
const scenario = ref('')
const step = ref(0)
const history = ref<HTMLElement | null>(null)
const sample = computed(() => scenarios[scenario.value]?.turns)
const nextSample = computed(() => sample.value?.[step.value])

watch(() => props.sessionId, () => {
  draft.value = ''
  speaker.value = '家长'
  step.value = 0
})
watch(scenario, () => { step.value = 0 })
watch(() => props.entries.map((entry) => entry.state), async () => {
  await nextTick()
  if (history.value) history.value.scrollTop = history.value.scrollHeight
})

function fill() {
  if (!nextSample.value) return
  speaker.value = nextSample.value.speaker
  draft.value = nextSample.value.text
}

async function send() {
  const turn = { text: draft.value.trim(), speaker: speaker.value }
  if (!turn.text || props.locked) return
  const fromSample = nextSample.value?.text === turn.text && nextSample.value?.speaker === turn.speaker
  if (await props.submit(turn)) {
    if (fromSample) step.value++
    draft.value = ''
  }
}
</script>

<template>
  <section class="panel conversation" aria-labelledby="conversation-title">
    <header class="panel-header">
      <span class="section-icon"><MessageCircle :size="19" /></span>
      <div><h2 id="conversation-title">咨询对话</h2><p>按发言角色记录多轮咨询</p></div>
      <span v-if="sessionId" class="pill header-extra">{{ entries.filter((item) => item.state === 'success').length }} 轮</span>
    </header>
    <div ref="history" class="conversation-history" role="log" aria-label="咨询记录">
      <div v-if="!entries.length" class="empty conversation-empty">
        <div class="chat-symbol"><MessageCircle :size="28" :stroke-width="1.6" /></div>
        <h3>{{ sessionId ? '等待第一轮发言' : '从一次咨询开始' }}</h3>
        <p>{{ sessionId ? '选择发言角色后输入内容，也可以填入演示场景。' : '点击右上角的新建会话，录入学生或家长的职业想法。' }}</p>
      </div>
      <article v-for="entry in entries" :key="entry.id" class="turn" :data-state="entry.state">
        <span :class="['avatar', entry.speaker === '学生' ? 'student' : entry.speaker === '咨询师' ? 'counselor' : 'parent']">{{ entry.speaker[0] }}</span>
        <div class="turn-content">
          <div class="turn-meta"><strong>{{ entry.speaker }}</strong><time>{{ entry.time }}</time></div>
          <div class="speech">{{ entry.text }}</div>
          <p v-if="entry.state === 'pending'" class="turn-state" role="status"><LoaderCircle class="spin" :size="13" />正在处理</p>
          <p v-else-if="entry.state === 'failure'" class="turn-state error-text" role="alert"><TriangleAlert :size="14" />提交失败 · {{ entry.error }}</p>
          <template v-else>
            <p class="turn-state success-text"><Check :size="14" />第 {{ entry.result.turn_no }} 轮已记录<span v-if="!entry.result.changes.length"> · 职业偏好无变更</span></p>
            <ul v-if="entry.result.changes.length || entry.result.pending_suggestions.length" class="turn-changes">
              <li v-for="change in entry.result.changes" :key="change.tag_id">
                <span>{{ change.new_value === null ? '移除' : change.old_value === null ? '新增' : '修改' }}</span>
                <strong>{{ tagName(change.tag_id) }}</strong>
                <template v-if="change.old_value !== null">{{ displayValue(change.old_value) }} → </template>{{ displayValue(change.new_value) }}
              </li>
              <li v-for="suggestion in entry.result.pending_suggestions" :key="suggestion.tag_id">
                <span>待确认</span><strong>{{ tagName(suggestion.tag_id) }}</strong>{{ displayValue(suggestion.value) }}
              </li>
            </ul>
          </template>
        </div>
      </article>
    </div>
    <form class="composer" @submit.prevent="send">
      <div class="sample-bar">
        <label class="sr-only" for="scenario">演示场景</label>
        <select id="scenario" v-model="scenario" :disabled="locked">
          <option value="">选择演示场景</option>
          <option v-for="(item, id) in scenarios" :key="id" :value="id">{{ item.label }}</option>
        </select>
        <button class="button button-plain" type="button" :disabled="!sessionId || !nextSample || locked || !!draft.trim()" @click="fill">
          <ArrowDownToLine :size="15" />{{ sample && !nextSample ? '场景已录完' : '填入下一轮' }}<span v-if="sample && nextSample" class="muted">{{ step + 1 }}/{{ sample.length }}</span>
        </button>
      </div>
      <div class="composer-box">
        <label class="sr-only" for="utterance">发言内容</label>
        <textarea id="utterance" v-model="draft" rows="3" :disabled="!sessionId || locked" :placeholder="sessionId ? '输入本轮发言，例如：希望以后在成都工作。' : '新建会话后可录入发言'" required></textarea>
        <div class="composer-actions">
          <label for="speaker">发言人</label>
          <select id="speaker" v-model="speaker" :disabled="!sessionId || locked"><option>家长</option><option>学生</option><option>咨询师</option></select>
          <button class="button button-primary send" type="submit" :disabled="!sessionId || locked || !draft.trim()"><ArrowUp :size="16" />提交发言</button>
        </div>
      </div>
    </form>
  </section>
</template>
