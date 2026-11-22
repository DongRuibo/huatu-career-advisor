<!--
岗位资料抽屉，读取岗位详情并共用新增与编辑表单。
编辑时只提交实际变化的字段，业务标签有改动时提交完整标签集以符合 PUT 接口约定。
-->
<script setup lang="ts">
import { BriefcaseBusiness, LoaderCircle, Pencil, Plus, Trash2, X } from '@lucide/vue'
import { onMounted, reactive, ref } from 'vue'
import { api, messageFor } from '../api'
import { tagLabels } from '../presentation'
import type { JobFields, JobRecordDetail, JobTagInput, JobWrite } from '../types'

const props = defineProps<{ jobId?: string; cities: string[]; directions: string[] }>()
const emit = defineEmits<{ close: []; saved: [detail: JobRecordDetail] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const record = ref<JobRecordDetail | null>(null)
const editing = ref(!props.jobId)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const textFields = [
  { key: 'name', label: '岗位名称' },
  { key: 'city', label: '工作城市', suggestions: 'record-cities' },
  { key: 'direction', label: '职业方向', suggestions: 'record-directions' },
  { key: 'unit_type', label: '单位类型' },
  { key: 'unit_name', label: '单位名称' },
  { key: 'education_req', label: '学历要求' },
  { key: 'major_req', label: '专业要求' },
  { key: 'political_req', label: '政治面貌要求' },
  { key: 'description', label: '工作内容', multiline: true },
  { key: 'remark', label: '备注', multiline: true },
] as const
const form = reactive({
  id: '',
  fields: Object.fromEntries(textFields.map(({ key }) => [key, ''])) as Record<(typeof textFields)[number]['key'], string>,
  experience: '' as number | '',
})
// 地区、方向及资格标签由岗位字段派生。编辑表单只列出可独立写入的业务标签。
const editableTags = Object.entries(tagLabels).filter(([id]) => id >= 'T003' && id <= 'T016')
const tagRows = ref<{ key: number; tagId: string; value: string; evidence: string }[]>([])
let nextTagKey = 0

// 初始化详情抽屉。新增直接展示空表单，查看时先读取真实记录。
onMounted(() => {
  dialog.value?.showModal()
  if (props.jobId) void load()
})

// 请求单个岗位的字段、标签依据与知识资料，失败后可重新查询。
async function load() {
  if (!props.jobId) return
  loading.value = true
  error.value = ''
  try {
    record.value = await api.getJob(props.jobId)
  } catch (reason) {
    error.value = messageFor(reason)
  } finally {
    loading.value = false
  }
}

// 进入编辑模式时复制原记录，取消编辑时保留后端返回的原始详情。
function edit() {
  if (!record.value) return
  form.id = record.value.job.job_id
  for (const { key } of textFields) form.fields[key] = record.value.job[key] ?? ''
  form.experience = record.value.job.experience_years ?? ''
  tagRows.value = record.value.tags.filter(({ tag_id }) => tag_id >= 'T003' && tag_id <= 'T016')
    .map(({ tag_id, value, evidence }) => ({ key: ++nextTagKey, tagId: tag_id, value, evidence: evidence ?? '' }))
  error.value = ''
  editing.value = true
}

// 新增一个待选择的业务标签，已有标签及依据继续留在表单中。
function addTag() {
  tagRows.value.push({ key: ++nextTagKey, tagId: '', value: '', evidence: '' })
}

// 保存中保持抽屉可见，让使用者看到写入结果及任何后端错误。
function close() {
  if (!saving.value) emit('close')
}

// 已有岗位回到详情，新建表单直接关闭。
function cancel() {
  if (!record.value) {
    close()
    return
  }
  error.value = ''
  editing.value = false
}

// 传入实际变化的字段和标签。失败时保留草稿，成功后用服务端结果替换详情。
async function save() {
  if (saving.value) return
  const previous = record.value
  const id = previous?.job.job_id ?? form.id.trim()
  if (!id) {
    error.value = '请填写岗位 ID。'
    return
  }

  const changes: Partial<JobFields> = {}
  for (const { key } of textFields) {
    const original = previous?.job[key] ?? null
    const value = form.fields[key].trim() || null
    if (form.fields[key] !== (original ?? '') && value !== original) changes[key] = value
  }
  const experience = form.experience === '' ? null : form.experience
  if (experience !== (previous?.job.experience_years ?? null)) changes.experience_years = experience

  const tags: Record<string, JobTagInput> = {}
  for (const row of tagRows.value) {
    const value = row.value.trim()
    if (!row.tagId || !value || row.tagId in tags) {
      error.value = '请为每条岗位标签选择不同的标签并填写有效取值。'
      return
    }
    tags[row.tagId] = { value, evidence: row.evidence.trim() || null }
  }
  const existingTags = previous?.tags.filter(({ tag_id }) => tag_id >= 'T003' && tag_id <= 'T016') ?? []
  const tagsChanged = existingTags.length !== tagRows.value.length || existingTags.some(
    ({ tag_id, value, evidence }) => tags[tag_id]?.value !== value.trim() || tags[tag_id]?.evidence !== (evidence?.trim() || null),
  )
  const payload: JobWrite = { ...changes, ...(tagsChanged ? { tags } : {}) }
  if (previous && !Object.keys(payload).length) {
    cancel()
    return
  }

  error.value = ''
  saving.value = true
  try {
    record.value = previous ? await api.updateJob(id, payload) : await api.createJob({ job_id: id, ...payload })
    editing.value = false
    emit('saved', record.value)
  } catch (reason) {
    error.value = messageFor(reason)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <dialog ref="dialog" class="offscreen-dialog detail-dialog record-dialog" aria-labelledby="record-title" @cancel.prevent="close">
    <div class="detail-sheet">
      <header class="dialog-header">
        <div class="detail-identity">
          <span class="section-icon"><BriefcaseBusiness :size="19" /></span>
          <div>
            <h2 id="record-title">{{ editing ? (record ? '编辑岗位' : '新增岗位') : (record?.job.name || '岗位详情') }}</h2>
            <p v-if="record">{{ record.job.job_id }}<template v-if="record.job.city"> · {{ record.job.city }}</template></p>
          </div>
        </div>
        <button class="icon-button" type="button" aria-label="关闭岗位资料" :disabled="saving" @click="close"><X :size="20" /></button>
      </header>

      <form v-if="editing" class="record-form" @submit.prevent="save">
        <div class="detail-content">
          <div class="record-fields">
            <label v-if="!record" class="wide-field">岗位 ID
              <input v-model="form.id" placeholder="例如：DEMO-001" required :disabled="saving" />
            </label>
            <label v-for="field in textFields" :key="field.key" :class="{ 'wide-field': 'multiline' in field }">
              {{ field.label }}
              <textarea v-if="'multiline' in field" v-model="form.fields[field.key]" rows="3" :disabled="saving" />
              <input v-else v-model="form.fields[field.key]" :list="'suggestions' in field ? field.suggestions : undefined" :disabled="saving" />
            </label>
            <label>经验年限要求
              <input v-model="form.experience" type="number" min="0" step="1" :disabled="saving" />
            </label>
          </div>
          <datalist id="record-cities"><option v-for="city in cities" :key="city" :value="city" /></datalist>
          <datalist id="record-directions"><option v-for="direction in directions" :key="direction" :value="direction" /></datalist>

          <section class="detail-section">
            <div class="tag-toolbar">
              <h3>业务标签</h3>
              <button class="button button-secondary" type="button" :disabled="saving || tagRows.length >= editableTags.length" @click="addTag">
                <Plus :size="15" />添加标签
              </button>
            </div>
            <p v-if="!tagRows.length" class="muted">暂无业务标签</p>
            <div v-for="(row, index) in tagRows" :key="row.key" class="tag-edit-row">
              <div class="tag-field">
                <label :for="`record-tag-${row.key}`">标签</label>
                <select :id="`record-tag-${row.key}`" v-model="row.tagId" required :disabled="saving">
                  <option value="">选择标签</option>
                  <option v-for="[id, name] in editableTags" :key="id" :value="id"
                    :disabled="tagRows.some((entry) => entry.key !== row.key && entry.tagId === id)">{{ name }}</option>
                </select>
              </div>
              <label>取值 <input v-model="row.value" :disabled="saving" required /></label>
              <button class="icon-button delete-tag" type="button" :aria-label="`移除第 ${index + 1} 条标签`" :disabled="saving"
                @click="tagRows.splice(index, 1)"><Trash2 :size="16" /></button>
              <label class="tag-evidence">事实依据 <input v-model="row.evidence" :disabled="saving" /></label>
            </div>
          </section>
          <p v-if="error" class="notice notice-error record-error" role="alert">{{ error }}</p>
        </div>
        <footer class="dialog-actions">
          <button class="button button-plain" type="button" :disabled="saving" @click="cancel">取消</button>
          <button class="button button-primary" type="submit" :disabled="saving">
            <LoaderCircle v-if="saving" class="spin" :size="15" />{{ saving ? '正在保存' : '保存岗位' }}
          </button>
        </footer>
      </form>

      <template v-else>
        <div class="detail-content">
          <p v-if="loading" class="state-line muted" role="status"><LoaderCircle class="spin" :size="15" />正在读取岗位详情</p>
          <div v-else-if="error" class="notice notice-error" role="alert">{{ error }}<button class="button button-plain" type="button" @click="load">重试</button></div>
          <template v-else-if="record">
            <dl class="record-facts">
              <div v-for="field in textFields" :key="field.key" :class="{ 'wide-field': 'multiline' in field }">
                <dt>{{ field.label }}</dt><dd>{{ record.job[field.key] ?? '未填写' }}</dd>
              </div>
              <div><dt>经验年限要求</dt><dd>{{ record.job.experience_years ?? '未填写' }}</dd></div>
              <div><dt>匹配状态</dt><dd><span class="pill" :class="record.job.is_matchable ? 'green' : 'amber'">{{ record.job.is_matchable ? '参与匹配' : '资料待补' }}</span></dd></div>
            </dl>
            <section class="detail-section">
              <h3>岗位标签与依据</h3>
              <p v-if="!record.tags.length" class="muted">暂无岗位标签</p>
              <div v-for="tag in record.tags" :key="tag.tag_id" class="assessment">
                <div class="assessment-title"><span class="pill">{{ tag.tag_id }}</span><strong>{{ tag.name }}</strong><span>{{ tag.value }}</span></div>
                <blockquote v-if="tag.evidence">{{ tag.evidence }}</blockquote>
                <p v-else class="muted">暂无依据</p>
              </div>
            </section>
            <section class="detail-section">
              <h3>知识资料</h3>
              <p v-if="!record.documents.length" class="muted">暂无关联资料</p>
              <details v-for="document in record.documents" :key="document.doc_id" class="source-details assessment">
                <summary class="source-toggle">{{ document.title }}</summary>
                <blockquote>{{ document.content }}</blockquote>
                <p v-if="document.usage_limit" class="muted">{{ document.usage_limit }}</p>
              </details>
            </section>
          </template>
        </div>
        <footer v-if="record" class="dialog-actions">
          <button class="button button-primary" type="button" @click="edit"><Pencil :size="15" />编辑岗位</button>
        </footer>
      </template>
    </div>
  </dialog>
</template>

<style scoped>
.record-dialog { width: min(620px, 100%); }
.record-form { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.record-fields, .record-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 14px; }
.wide-field { grid-column: 1 / -1; }
.record-fields label, .tag-edit-row label { display: grid; gap: 4px; color: var(--muted); font-size: 12px; font-weight: 650; }
.record-fields :is(input, textarea), .tag-edit-row :is(input, select) { width: 100%; min-width: 0; color: var(--ink); }
.record-fields textarea { padding: 8px 10px; }
.record-facts > div { padding: 8px 10px; border: 1px solid var(--line); background: #fafafe; border-radius: 8px; }
.record-facts dt { font-size: 12px; margin-bottom: 2px; }
.record-facts dd { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; }
.tag-toolbar { display: flex; align-items: baseline; justify-content: space-between; gap: 6px; margin-bottom: 8px; }
.tag-edit-row { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr) auto; align-items: end; gap: 7px; margin-bottom: 8px; padding: 8px; border: 1px solid var(--line); border-radius: 8px; }
.tag-field { display: grid; gap: 4px; }
.tag-evidence { grid-column: 1 / 3; }
.delete-tag { margin-bottom: 3px; }
.record-error { margin-top: 10px; }
.detail-sheet > .dialog-header, .dialog-actions { flex-shrink: 0; }
@media (max-width: 430px) {
  .record-fields, .record-facts { gap: 8px; }
  .record-dialog { border-radius: 0; }
}
</style>
