// 界面展示转换层，集中提供标签、约束与资格状态的中文名称。
// 用于格式化多选、加班复合值和得分，保留后端的实际匹配结果。

import type { AssessmentStatus, ConstraintType, PreferenceValue, QualificationHint } from './types'

export const tagLabels: Record<string, string> = {
  T001: '意向地区', T002: '职业方向', T003: '出差频率', T004: '工作稳定性', T005: '群众接触程度',
  T006: '工作节奏', T007: '办公方式', T008: '收入相对水平', T009: '社会资源与职业接触机会',
  T010: '成长发展空间', T011: '跨部门协作频率', T012: '工作内容主类型', T013: '加班频率',
  T014: '工作压力', T015: '工作地点属性', T016: '工作自主性', T017: '学历要求', T018: '经验年限要求',
  T019: '政治面貌要求', T020: '专业要求',
}

export const qualificationLabels = { education: '学历', political_status: '政治面貌', experience_years: '工作经验' }

export const constraintLabels: Record<ConstraintType, string> = {
  eq: '等于', in: '包含其一', max: '不超过', min: '至少', prefer: '偏好',
}

export const statusLabels: Record<AssessmentStatus, string> = {
  confirmed: '已确认', pending_confirmation: '待确认', matched: '满足', partially_matched: '部分满足', unmet: '未满足',
}

export const qualificationStatus: Record<QualificationHint['status'], string> = {
  meets_requirement: '符合参考要求', preparation_needed: '可提前准备', pending_confirmation: '待确认', reference_only: '参考要求',
}

// 把标签 ID 转成中文名。未收录的标签回退到原 ID，便于查看后端数据。
export function tagName(id: string): string {
  return tagLabels[id] ?? id
}

// 把标量、多选、复合值和待确认避开建议转为可读文字，必要时加上限或下限词。
export function displayValue(value: PreferenceValue | null, constraint?: ConstraintType): string {
  if (value === null) return '未设置'
  if (Array.isArray(value)) return value.join('、')
  if (typeof value === 'object') {
    if ('avoid' in value) return `倾向避开${value.avoid}`
    return `首选${value.preferred}，可接受至${value.acceptable_max}`
  }
  if (constraint === 'max' || constraint === 'min') return `${constraintLabels[constraint]}${value}`
  return value
}

// 将后端分值保留两位小数，空值单独显示为未评分。
export function scoreText(score: number | null): string {
  return score === null ? '未评分' : score.toFixed(2)
}
