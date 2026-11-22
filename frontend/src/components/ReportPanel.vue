<!--
规划报告面板，允许选择格式和推荐数量，并按需生成、展示和复制报告。
按格式缓存结果并记录画像版本。Markdown 通过禁用原始 HTML 的渲染器展示。
-->
<script setup lang="ts">
import { Clipboard, FileText, LoaderCircle } from '@lucide/vue'
import MarkdownIt from 'markdown-it'
import { computed, ref } from 'vue'
import { api, messageFor } from '../api'

const props = defineProps<{ sessionId?: string; revision: number; locked: boolean }>()
// 展示用户报告中的标题、表格和列表，关闭原始 HTML 和图片渲染。
const renderer = new MarkdownIt({ html: false }).disable('image')
const format = ref<'md' | 'json'>('md')
const top = ref<number | string>(5)
const documents = ref<Partial<Record<'md' | 'json', { text: string; revision: number; top: number }>>>({})
// 按当前选择的格式取出已经生成的报告。
const current = computed(() => documents.value[format.value])
// 将当前 Markdown 文本转为展示内容，JSON 格式保持文本。
const rendered = computed(() => format.value === 'md' && current.value ? renderer.render(current.value.text) : '')
const generating = ref(false)
const error = ref('')
const copyStatus = ref('')

// 校验推荐数量后按格式请求报告，缓存结果和对应画像版本。
async function generate() {
  if (!props.sessionId || props.locked || generating.value) return
  const resultFormat = format.value
  // 保存生成时的格式、数量和画像版本，以便在设置变化后标识旧报告。
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

// 复制当前报告的原始文本，并显示剪贴板操作是否成功。
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
          <button type="button" :aria-pressed="format === 'md'" :disabled="generating" @click="format = 'md'; copyStatus = ''; error = ''">用户报告</button>
          <button type="button" :aria-pressed="format === 'json'" :disabled="generating" @click="format = 'json'; copyStatus = ''; error = ''">审计明细（JSON）</button>
        </div>
        <label for="report-top">推荐数量</label><input id="report-top" v-model="top" type="number" min="1" step="1" required :disabled="generating" />
        <button class="button button-primary" type="submit" :disabled="!sessionId || locked || generating"><LoaderCircle v-if="generating" class="spin" :size="16" />{{ generating ? '正在生成' : format === 'md' ? '生成报告' : '生成明细' }}</button>
        <button v-if="current" class="button button-plain copy-report" type="button" @click="copy"><Clipboard :size="15" />{{ format === 'md' ? '复制报告' : '复制明细' }}</button>
      </form>
      <p v-if="error" class="notice notice-error" role="alert">报告生成失败 · {{ error }}</p>
      <p v-if="copyStatus" class="muted state-line" role="status">{{ copyStatus }}</p>
      <p v-if="current && (current.revision !== revision || current.top !== Number(top))" class="muted state-line">当前显示上一份报告，可按最新画像和推荐数量重新生成。</p>
      <!-- 只插入 MarkdownIt 生成的内容，原始 HTML 和图片语法已关闭。 -->
      <article v-if="current && format === 'md'" class="report-output markdown" v-html="rendered"></article>
      <pre v-else-if="current" class="report-output json-output">{{ current.text }}</pre>
      <p v-else class="report-empty">{{ sessionId ? '填写推荐数量后即可生成。' : '创建会话后可生成报告。' }}</p>
    </div>
  </section>
</template>
