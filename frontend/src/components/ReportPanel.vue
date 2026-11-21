<script setup lang="ts">
import { Clipboard, FileText, LoaderCircle } from '@lucide/vue'
import MarkdownIt from 'markdown-it'
import { computed, ref } from 'vue'
import { api, messageFor } from '../api'

const props = defineProps<{ sessionId?: string; revision: number; locked: boolean }>()
const renderer = new MarkdownIt({ html: false }).disable('image')
const format = ref<'md' | 'json'>('md')
const top = ref<number | string>(5)
const documents = ref<Partial<Record<'md' | 'json', { text: string; revision: number; top: number }>>>({})
const current = computed(() => documents.value[format.value])
const rendered = computed(() => format.value === 'md' && current.value ? renderer.render(current.value.text) : '')
const generating = ref(false)
const error = ref('')
const copyStatus = ref('')

async function generate() {
  if (!props.sessionId || props.locked || generating.value) return
  const resultFormat = format.value
  const revision = props.revision
  const amount = Number(top.value)
  if (!Number.isSafeInteger(amount) || amount <= 0) {
    error.value = '请填写大于 0 的整数。'
    return
  }
  error.value = ''
  copyStatus.value = ''
  generating.value = true
  try {
    const text = resultFormat === 'md'
      ? await api.getMarkdown(props.sessionId, amount)
      : JSON.stringify(await api.getJsonReport(props.sessionId, amount), null, 2)
    documents.value[resultFormat] = { text, revision, top: amount }
  } catch (reason) {
    error.value = messageFor(reason)
  } finally {
    generating.value = false
  }
}

async function copy() {
  if (!current.value) return
  try {
    await navigator.clipboard.writeText(current.value.text)
    copyStatus.value = '已复制'
  } catch {
    copyStatus.value = '复制失败，可选中文本后手动复制。'
  }
}
</script>

<template>
  <section class="panel" aria-labelledby="report-title" :aria-busy="generating">
    <header class="panel-header">
      <span class="section-icon"><FileText :size="19" /></span>
      <div><h2 id="report-title">职业规划报告</h2><p>汇总咨询意向、岗位推荐及准备建议</p></div>
    </header>
    <div class="panel-body report-body">
      <form class="report-tools" @submit.prevent="generate">
        <div class="segmented" role="group" aria-label="报告格式">
          <button type="button" :aria-pressed="format === 'md'" :disabled="generating" @click="format = 'md'; copyStatus = ''; error = ''">Markdown</button>
          <button type="button" :aria-pressed="format === 'json'" :disabled="generating" @click="format = 'json'; copyStatus = ''; error = ''">JSON</button>
        </div>
        <label for="report-top">推荐数量</label><input id="report-top" v-model="top" type="number" min="1" step="1" required :disabled="generating" />
        <button class="button button-primary" type="submit" :disabled="!sessionId || locked || generating"><LoaderCircle v-if="generating" class="spin" :size="16" />{{ generating ? '正在生成' : '生成报告' }}</button>
        <button v-if="current" class="button button-plain copy-report" type="button" @click="copy"><Clipboard :size="15" />复制报告</button>
      </form>
      <p v-if="error" class="notice notice-error" role="alert">报告生成失败 · {{ error }}</p>
      <p v-if="copyStatus" class="muted state-line" role="status">{{ copyStatus }}</p>
      <p v-if="current && (current.revision !== revision || current.top !== Number(top))" class="muted state-line">当前显示上一份报告，可按最新画像和推荐数量重新生成。</p>
      <!-- 只插入 MarkdownIt 生成的内容，原始 HTML 和图片语法已关闭。 -->
      <article v-if="current && format === 'md'" class="report-output markdown" v-html="rendered"></article>
      <pre v-else-if="current" class="report-output json-output">{{ current.text }}</pre>
      <p v-else class="report-empty">{{ sessionId ? '选择格式和推荐数量后，可生成该会话的报告。' : '创建会话后可生成报告。' }}</p>
    </div>
  </section>
</template>
