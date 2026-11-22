<!--
岗位库页面，通过现有 API 筛选岗位、打开详情并在保存后重新查询。
未筛选的岗位记录用于提供城市和方向选项，咨询会话有独立的状态。
-->
<script setup lang="ts">
import { BriefcaseBusiness, ChevronRight, LoaderCircle, Plus, Search } from '@lucide/vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { api, messageFor } from '../api'
import { tagLabels } from '../presentation'
import type { JobFilters, JobRecord, JobRecordDetail } from '../types'
import JobRecordDialog from './JobRecordDialog.vue'

const emit = defineEmits<{ changed: [] }>()
const draft = reactive({ city: '', direction: '', tagId: '', tagValue: '' })
const applied = ref<JobFilters>({})
const rows = ref<JobRecord[] | null>(null)
const catalog = ref<JobRecord[]>([])
const loading = ref(false)
const error = ref('')
const status = ref('')
const editor = ref<{ id?: string } | null>(null)

// 从未筛选的真实岗位中整理已使用的城市和职业方向，避免筛选后选项变少。
const options = computed(() => ({
  cities: [...new Set(catalog.value.flatMap((job) => job.city ? [job.city] : []))].sort(),
  directions: [...new Set(catalog.value.flatMap((job) => job.direction ? [job.direction] : []))].sort(),
}))

// 通过服务端执行组合筛选。失败时保留上一次成功查询的岗位。
async function load(filters: JobFilters) {
  if (loading.value) return false
  loading.value = true
  error.value = ''
  try {
    const result = await api.listJobs(filters)
    rows.value = result
    applied.value = filters
    if (!Object.keys(filters).length) catalog.value = result
    return true
  } catch (reason) {
    error.value = messageFor(reason)
    return false
  } finally {
    loading.value = false
  }
}

// 拼装后端支持的可选条件。选择标签后同时提交它的取值。
function search() {
  const value = draft.tagValue.trim()
  if (draft.tagId && !value) {
    error.value = '请输入要精确匹配的标签值。'
    return
  }
  status.value = ''
  void load({
    ...(draft.city ? { city: draft.city } : {}),
    ...(draft.direction ? { direction: draft.direction } : {}),
    ...(draft.tagId ? { tag: `${draft.tagId}:${value}` } : {}),
  })
}

// 清空筛选草稿并恢复岗位库列表。
function reset() {
  Object.assign(draft, { city: '', direction: '', tagId: '', tagValue: '' })
  search()
}

// 保存成功后读取当前筛选结果，岗位已移出筛选时仍保留刚保存的详情。
async function onSaved({ job }: JobRecordDetail) {
  catalog.value = [...catalog.value.filter((item) => item.job_id !== job.job_id), job]
  emit('changed')
  status.value = `岗位 ${job.job_id} 已保存。`
  if (await load(applied.value) && !rows.value?.some((item) => item.job_id === job.job_id)) {
    status.value = `岗位 ${job.job_id} 已保存，当前筛选结果不包含该岗位。`
  }
}

// 第一次进入岗位管理时加载岗位库。
onMounted(() => load({}))
</script>

<template>
  <section aria-labelledby="job-management-title">
    <div class="workspace-intro job-management-intro">
      <div>
        <h1 id="job-management-title">岗位管理</h1>
        <p>按条件查询岗位库，查看标签依据并维护岗位资料。</p>
      </div>
      <button class="button button-primary new-session" type="button" :disabled="loading" @click="editor = {}">
        <Plus :size="17" />新增岗位
      </button>
    </div>

    <section class="panel" aria-labelledby="catalog-title">
      <header class="panel-header">
        <span class="section-icon"><BriefcaseBusiness :size="19" /></span>
        <div><h2 id="catalog-title">岗位库</h2></div>
        <span v-if="rows" class="header-extra catalog-count" role="status"><strong>{{ rows.length }}</strong> 个岗位</span>
      </header>

      <div class="panel-body">
        <form class="filters" @submit.prevent="search">
          <div class="filter-field">
            <label for="jobs-city">工作城市</label>
            <select id="jobs-city" v-model="draft.city" :disabled="loading">
              <option value="">全部城市</option>
              <option v-for="city in options.cities" :key="city" :value="city">{{ city }}</option>
            </select>
          </div>
          <div class="filter-field">
            <label for="jobs-direction">职业方向（行业）</label>
            <select id="jobs-direction" v-model="draft.direction" :disabled="loading">
              <option value="">全部方向</option>
              <option v-for="direction in options.directions" :key="direction" :value="direction">{{ direction }}</option>
            </select>
          </div>
          <div class="filter-field">
            <label for="jobs-tag">岗位标签</label>
            <select id="jobs-tag" v-model="draft.tagId" :disabled="loading" @change="draft.tagValue = ''">
              <option value="">不限标签</option>
              <option v-for="(name, id) in tagLabels" :key="id" :value="id">{{ name }}</option>
            </select>
          </div>
          <div class="filter-field">
            <label for="jobs-tag-value">标签值</label>
            <input id="jobs-tag-value" v-model="draft.tagValue" :disabled="loading || !draft.tagId" :required="!!draft.tagId"
              :placeholder="draft.tagId === 'T005' ? '例如：多' : '输入准确取值'" />
          </div>
          <div class="filter-actions">
            <button class="button button-primary" type="submit" :disabled="loading"><Search :size="15" />查询岗位</button>
            <button class="button button-plain" type="button" :disabled="loading" @click="reset">重置</button>
          </div>
        </form>

        <p v-if="error" class="notice notice-error" role="alert">{{ error }}{{ rows ? ' · 保留上次查询结果' : '' }}</p>
        <p v-if="status" class="notice save-notice" role="status">{{ status }}</p>
        <p v-if="loading" class="state-line muted" role="status"><LoaderCircle class="spin" :size="15" />正在查询岗位</p>
        <div v-else-if="rows?.length === 0" class="empty catalog-empty">
          <Search :size="23" />
          <h3>{{ Object.keys(applied).length ? '没有符合条件的岗位' : '岗位库为空' }}</h3>
          <p>{{ Object.keys(applied).length ? '调整筛选条件后再试。' : '导入模拟数据或新增一个岗位。' }}</p>
        </div>

        <div v-if="rows?.length" class="catalog-table" tabindex="0" aria-label="岗位查询结果">
          <table>
            <thead><tr><th scope="col">岗位</th><th scope="col">城市</th><th scope="col">职业方向</th><th scope="col" class="unit-column">单位类型</th><th scope="col">匹配状态</th><th scope="col" class="open-column"><span class="sr-only">查看</span></th></tr></thead>
            <tbody>
              <tr v-for="job in rows" :key="job.job_id">
                <th scope="row">
                  <button class="catalog-job" type="button" :disabled="loading" @click="editor = { id: job.job_id }">
                    <strong>{{ job.name || '未填写岗位名称' }}</strong>
                    <span>{{ job.job_id }}</span>
                  </button>
                </th>
                <td>{{ job.city || '未填写' }}</td>
                <td>{{ job.direction || '未填写' }}</td>
                <td class="unit-column">{{ job.unit_type || '未填写' }}</td>
                <td><span class="pill" :class="job.is_matchable ? 'green' : 'amber'">{{ job.is_matchable ? '参与匹配' : '资料待补' }}</span></td>
                <td class="open-column"><button class="icon-button" type="button" :disabled="loading" :aria-label="`查看岗位 ${job.job_id}`" @click="editor = { id: job.job_id }"><ChevronRight :size="16" /></button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <Teleport to="body">
      <JobRecordDialog v-if="editor" :job-id="editor.id" :cities="options.cities" :directions="options.directions"
        @close="editor = null" @saved="onSaved" />
    </Teleport>
  </section>
</template>

<style scoped>
.job-management-intro { align-items: flex-end; }
.catalog-count { color: var(--muted); font-size: 13px; }
.catalog-count strong { color: var(--accent-dark); font-size: 18px; font-variant-numeric: tabular-nums; }
.filters { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)) auto; align-items: end; gap: 12px; margin-bottom: 16px; }
.filter-field { display: grid; gap: 5px; color: var(--muted); font-size: 12px; font-weight: 650; }
.filters :is(input, select) { width: 100%; min-height: 35px; }
.filter-actions { display: flex; gap: 4px; }
.catalog-table { border: 1px solid var(--line); border-radius: 10px; overflow-x: auto; }
.catalog-table table { border-collapse: collapse; width: 100%; min-width: 470px; text-align: left; }
.catalog-table thead { color: var(--muted); background: #f6f6fb; font-size: 12px; }
.catalog-table th, .catalog-table td { padding: 10px 14px; border-bottom: 1px solid var(--line); }
.catalog-table tbody tr:last-child > * { border-bottom: 0; }
.catalog-table tbody tr:hover { background: #faf9ff; }
.catalog-table tbody th { padding-block: 7px; }
.catalog-table td { font-size: 13px; }
.catalog-job { display: grid; gap: 2px; justify-items: start; text-align: left; color: var(--ink); border: 0; background: none; padding: 2px 0; }
.catalog-job:hover { color: var(--accent-dark); }
.catalog-job span { color: var(--muted); font-size: 11px; font-weight: 500; }
.open-column { width: 1%; }
.catalog-empty { min-height: 155px; }
.save-notice { color: var(--green); background: #eaf7f0; }
@media (max-width: 900px) { .filters { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 640px) {
  .job-management-intro { align-items: flex-start; }
  .unit-column { display: none; }
  .catalog-table th, .catalog-table td { padding-inline: 9px; }
  .catalog-table table { min-width: 365px; }
}
</style>
