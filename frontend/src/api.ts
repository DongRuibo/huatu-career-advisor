import type { FollowUpQuestion, JobExplanation, JobMatch, PlanningReport, PreferenceUpdate, Profile, Session, TurnResult, Utterance } from './types'

function errorDetail(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object' || !('detail' in payload)) return null
  const detail = payload.detail
  if (typeof detail === 'string') return detail
  if (!Array.isArray(detail)) return null
  return detail.map((issue) => {
    if (!issue || typeof issue.msg !== 'string') return '请求参数无效'
    const field = Array.isArray(issue.loc) ? issue.loc.slice(1).join('.') : ''
    return field ? `${field} · ${issue.msg}` : issue.msg
  }).join('；') || null
}

export function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请重试。'
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, init)
  } catch {
    throw new Error('无法连接 FastAPI，请确认后端已启动。')
  }
  if (response.ok) return response

  const payload: unknown = await response.json().catch(() => null)
  const detail = errorDetail(payload)
  if (detail) throw new Error(`${detail}（HTTP ${response.status}）`)
  if (response.status >= 500) throw new Error(`后端暂时不可用，请查看 FastAPI 终端并重试（HTTP ${response.status}）。`)
  throw new Error(`请求失败（HTTP ${response.status}）。`)
}

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

const sessionPath = (id: string) => `/sessions/${encodeURIComponent(id)}`
const reportPath = (id: string, format: 'md' | 'json', top: number) =>
  `${sessionPath(id)}/report?${new URLSearchParams({ format, top_n: String(top) })}`

export const api = {
  createSession: () => json<Session>('/sessions', 'POST'),
  submitTurn: (id: string, turn: Utterance) => json<TurnResult>(`${sessionPath(id)}/turns`, 'POST', turn),
  getProfile: (id: string) => json<Profile>(`${sessionPath(id)}/preferences`),
  updatePreference: (id: string, tag: string, payload: PreferenceUpdate) =>
    json<Profile>(`${sessionPath(id)}/preferences/${encodeURIComponent(tag)}`, 'PUT', payload),
  getMatches: (id: string) => json<JobMatch[]>(`${sessionPath(id)}/matches`),
  explainJob: (id: string, jobId: string) =>
    json<JobExplanation>(`${sessionPath(id)}/jobs/${encodeURIComponent(jobId)}/explain`),
  getQuestions: (id: string) => json<FollowUpQuestion[]>(`${sessionPath(id)}/questions`),
  getMarkdown: async (id: string, top: number) => (await request(reportPath(id, 'md', top))).text(),
  getJsonReport: (id: string, top: number) => json<PlanningReport>(reportPath(id, 'json', top)),
}
