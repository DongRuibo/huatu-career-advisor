<!--
偏好编辑弹窗，将输入整理为标签所需的单值、多选或加班复合值。
负责表单校验和错误提示，交由父组件调用偏好接口并刷新匹配。
-->
<script setup lang="ts">
import { X } from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'
import { messageFor } from '../api'
import { constraintLabels, tagLabels } from '../presentation'
import type { ConstraintType, EditableValue, Preference, PreferenceUpdate, Strength } from '../types'

const props = defineProps<{
  tagId?: string
  preferences: Record<string, Preference>
  update: (tagId: string, update: PreferenceUpdate) => Promise<boolean>
}>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const tag = ref(props.tagId ?? 'T001')
const editableTags = Object.entries(tagLabels).slice(0, 16)
const strength = ref<Strength>('hard')
const constraint = ref<ConstraintType>('in')
const value = ref('')
const maximum = ref('')
const saving = ref(false)
const error = ref('')
// 按标签确定允许的硬条件约束，普通软偏好标签返回空值。
const hardConstraint = computed<ConstraintType | null>(() => tag.value === 'T001' || tag.value === 'T002' ? 'in' : tag.value === 'T003' ? 'max' : null)
const listValue = computed(() => hardConstraint.value === 'in' || tag.value === 'T012')

// 选中标签后为表单加载该标签已保存的值和约束，或应用新建默认值。
watch(tag, () => {
  const existing = props.preferences[tag.value]
  // 编辑时加载已保存的强度与约束。新增时才根据标签设置起始值。
  strength.value = existing?.strength ?? (hardConstraint.value ? 'hard' : 'soft')
  constraint.value = existing?.constraint_type ?? hardConstraint.value ?? 'prefer'
  const current = existing?.value
  value.value = typeof current === 'string' ? current : Array.isArray(current) ? current.join('、') : current && 'preferred' in current ? current.preferred : ''
  maximum.value = current && typeof current === 'object' && 'acceptable_max' in current ? current.acceptable_max : ''
  error.value = ''
}, { immediate: true })
// 切换为硬条件时将表单约束调整为该标签要求的类型。
watch(strength, () => {
  if (strength.value === 'hard' && hardConstraint.value) constraint.value = hardConstraint.value
})

// 表单挂载后使用原生对话框显示偏好编辑界面。
onMounted(() => dialog.value?.showModal())

// 在没有保存请求进行时通知父组件关闭编辑弹窗。
function close() {
  if (!saving.value) emit('close')
}

// 把表单值整理为接口支持的格式，保存成功后关闭弹窗，失败则显示错误并保留输入。
async function save() {
  const cleaned = value.value.trim()
  const upper = maximum.value.trim()
  // 地区和方向始终用数组。工作类型仍可保存单值，多个选项时用数组。
  const choices = listValue.value ? cleaned.split(/[,，、\n]/u).map((entry) => entry.trim()).filter(Boolean) : null
  const finalValue: EditableValue = choices
    ? tag.value === 'T012' && choices.length === 1 ? choices[0] : choices
    : tag.value === 'T013' && upper ? { preferred: cleaned, acceptable_max: upper } : cleaned
  if (!cleaned || (Array.isArray(finalValue) && !finalValue.length)) {
    error.value = '请输入偏好值。'
    return
  }
  error.value = ''
  saving.value = true
  try {
    if (await props.update(tag.value, { action: 'set', value: finalValue, strength: strength.value, constraint_type: constraint.value })) emit('close')
  } catch (reason) {
    error.value = messageFor(reason)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <dialog ref="dialog" class="offscreen-dialog" aria-labelledby="edit-title" @cancel.prevent="close">
    <form class="editor" @submit.prevent="save">
      <header class="dialog-header"><div><h2 id="edit-title">{{ tagId ? '修改偏好' : '添加偏好' }}</h2><p>保存后自动重新匹配岗位</p></div><button type="button" class="icon-button" aria-label="关闭编辑" :disabled="saving" @click="close"><X :size="20" /></button></header>
      <div class="editor-fields">
        <label for="edit-tag">偏好标签</label><select id="edit-tag" v-model="tag" :disabled="saving || !!tagId"><option v-for="[id, name] in editableTags" :key="id" :value="id">{{ name }}</option></select>
        <div class="editor-grid">
          <div><label for="edit-strength">强度</label><select id="edit-strength" v-model="strength" :disabled="saving || !hardConstraint"><option v-if="hardConstraint" value="hard">硬条件</option><option value="soft">软偏好</option></select></div>
          <div><label for="edit-constraint">约束</label><select id="edit-constraint" v-model="constraint" :disabled="saving || strength === 'hard'"><option v-for="(name, id) in constraintLabels" :key="id" :value="id">{{ name }}</option></select></div>
        </div>
        <label for="edit-value">偏好值</label><input id="edit-value" v-model="value" :disabled="saving" :placeholder="tag === 'T001' ? '例如：成都、重庆' : tag === 'T003' ? '例如：偶尔' : undefined" required />
        <p v-if="listValue" class="muted">可用顿号或逗号分隔多个选项。</p>
        <template v-if="tag === 'T013'"><label for="max-overtime">可接受的最大加班频率（可选）</label><input id="max-overtime" v-model="maximum" :disabled="saving" /></template>
        <p v-if="error" class="notice notice-error" role="alert">{{ error }}</p>
      </div>
      <div class="dialog-actions"><button type="button" class="button button-secondary" :disabled="saving" @click="close">取消</button><button class="button button-primary" :disabled="saving">{{ saving ? '正在保存' : '保存偏好' }}</button></div>
    </form>
  </dialog>
</template>
