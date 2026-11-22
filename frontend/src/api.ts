// 前端 API 客户端，封装咨询流程和岗位资料的查询、写入。
// 按响应格式解析 JSON 或 Markdown，并将业务错误和字段校验错误转换为界面提示。

import type {
  FollowUpQuestion, JobExplanation, JobFilters, JobMatch, JobRecord, JobRecordDetail, JobWrite,
  PlanningReport, PreferenceUpdate, Profile, Session, TurnResult, Utterance,
} from './types'

// 将 FastAPI 的业务错误和参数校验错误转换成统一的界面提示。
function errorDetail(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object' || !('detail' in payload)) return null
  const detail = payload.detail
  if (typeof detail === 'string') return detail
  if (!Array.isArray(detail)) return null
  // 逐项提取校验字段与报错内容，拼出可读的定位信息。
  return detail.map((issue) => {
    if (!issue || typeof issue.msg !== 'string') return '请求参数无效'
    const field = Array.isArray(issue.loc) ? issue.loc.slice(1).join('.') : ''
    return field ? `${field} · ${issue.msg}` : issue.msg
  }).join('；') || null
}

// 把任意捕获值转换为可在界面展示的错误文本。
export function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请重试。'
}

// 发送同源代理请求，成功时返回原始响应，失败时保留可识别的后端错误详情。
async function request(path: string, init?: RequestInit): Promise<Response> {
  let response: Response
  try {
    // 开发环境由 Vite 将此路径转发给 FastAPI。
    response = await fetch(`/api${path}`, init)
  } catch {
    throw new Error('无法连接 FastAPI，请确认后端已启动。')
  }
  if (response.ok) return response

  // 错误响应可能是普通文本。解析失败时交给 HTTP 状态码分支生成提示。
  const payload: unknown = await response.json().catch(() => null)
  const detail = errorDetail(payload)
  if (detail) throw new Error(`${detail}（HTTP ${response.status}）`)
  if (response.status >= 500) throw new Error(`后端暂时不可用，请查看 FastAPI 终端并重试（HTTP ${response.status}）。`)
  throw new Error(`请求失败（HTTP ${response.status}）。`)
}

// 按需编码 JSON 请求体并解析 JSON 响应，报告的 Markdown 内容由独立入口处理。
async function json<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await request(path, {
    method,
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  })
  try {
    return await response.json() as T
  } catch {
    throw new Error('后端返回的数据格式不正确。')
  }
}

// 构造会话路由前缀，对会话 ID 编码后再加入路径。
const sessionPath = (id: string) => `/sessions/${encodeURIComponent(id)}`
// 使用查询参数传递报告格式与最多显示的推荐数。
const reportPath = (id: string, format: 'md' | 'json', top: number) =>
  `${sessionPath(id)}/report?${new URLSearchParams({ format, top_n: String(top) })}`

export const api = {
  // 创建独立会话，返回后续请求使用的会话 ID。
  createSession: () => json<Session>('/sessions', 'POST'),
  // 向当前会话提交发言角色和原话，读取本轮轮次与画像变更。
  submitTurn: (id: string, turn: Utterance) => json<TurnResult>(`${sessionPath(id)}/turns`, 'POST', turn),
  // 读取当前正式偏好、资格、修正历史与待确认建议。
  getProfile: (id: string) => json<Profile>(`${sessionPath(id)}/preferences`),
  // 按标签手动设定偏好或确认建议，返回更新后的完整画像。
  updatePreference: (id: string, tag: string, payload: PreferenceUpdate) =>
    json<Profile>(`${sessionPath(id)}/preferences/${encodeURIComponent(tag)}`, 'PUT', payload),
  // 读取当前画像对应的候选岗位、得分、证据及资格提示。
  getMatches: (id: string) => json<JobMatch[]>(`${sessionPath(id)}/matches`),
  // 按会话和岗位 ID 请求讲解，返回引用与资料缺失说明。
  explainJob: (id: string, jobId: string) =>
    json<JobExplanation>(`${sessionPath(id)}/jobs/${encodeURIComponent(jobId)}/explain`),
  // 以当前匹配候选为范围获取咨询师可继续提出的追问。
  getQuestions: (id: string) => json<FollowUpQuestion[]>(`${sessionPath(id)}/questions`),
  // 读取指定推荐数量的 Markdown 报告文本，用于渲染和复制。
  getMarkdown: async (id: string, top: number) => (await request(reportPath(id, 'md', top))).text(),
  // 读取相同报告接口的 JSON 格式，供结构化展示。
  getJsonReport: (id: string, top: number) => json<PlanningReport>(reportPath(id, 'json', top)),
  // 按城市、方向和单项标签查询岗位库，查询参数由 URLSearchParams 编码。
  listJobs: (filters: JobFilters = {}) => {
    const query = new URLSearchParams(filters).toString()
    return json<JobRecord[]>(query ? `/jobs?${query}` : '/jobs')
  },
  // 按岗位 ID 读取数据库字段、标签依据和知识资料。
  getJob: (id: string) => json<JobRecordDetail>(`/jobs/${encodeURIComponent(id)}`),
  // 新增岗位并读取数据库同步后的完整详情。
  createJob: (body: JobWrite & { job_id: string }) => json<JobRecordDetail>('/jobs', 'POST', body),
  // 更新现有岗位，调用方只传本次修改的字段。
  updateJob: (id: string, body: JobWrite) => json<JobRecordDetail>(`/jobs/${encodeURIComponent(id)}`, 'PUT', body),
}
