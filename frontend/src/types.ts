export type Speaker = '家长' | '学生' | '咨询师'
export interface Utterance { speaker: Speaker; text: string }
export interface Session { session_id: string; mode: string }

export type Strength = 'hard' | 'soft'
export type ConstraintType = 'eq' | 'in' | 'max' | 'min' | 'prefer'
export interface Overtime { preferred: string; acceptable_max: string }
export interface Avoidance { avoid: string; needs_confirmation: boolean }
export type EditableValue = string | string[] | Overtime
export type PreferenceValue = EditableValue | Avoidance

export interface Preference {
  tag_id: string
  value: PreferenceValue
  strength: Strength
  constraint_type: ConstraintType
  evidence_turn_id: number | null
  evidence_text: string | null
}

export interface PreferenceChange {
  tag_id: string
  old_value: PreferenceValue | null
  new_value: PreferenceValue | null
  reason: string
  evidence_turn_id: number | null
  turn_no: number | null
}

export interface Suggestion {
  tag_id: string
  value: Avoidance
  evidence_turn_id: number | null
  evidence_text: string | null
}

export interface Qualifications {
  education?: string
  political_status?: string
  experience_years?: number
}

export interface Profile {
  preferences: Record<string, Preference>
  qualifications: Qualifications
  changes: PreferenceChange[]
  pending_suggestions: Record<string, Suggestion>
}

export interface TurnResult {
  turn_id: number
  turn_no: number
  changes: PreferenceChange[]
  qualifications: Qualifications
  pending_suggestions: Suggestion[]
}

export type DialogueEntry = Utterance & { id: number; time: string } & (
  | { state: 'pending' }
  | { state: 'success'; result: TurnResult }
  | { state: 'failure'; error: string }
)

export interface SetPreference {
  action: 'set'
  value: EditableValue
  strength: Strength
  constraint_type: ConstraintType
}

export type PreferenceUpdate = SetPreference | { action: 'confirm'; evidence_turn_id: number }

export type HardStatus = 'confirmed' | 'pending_confirmation'
export type AssessmentStatus = HardStatus | 'matched' | 'partially_matched' | 'unmet'
export interface Assessment {
  tag_id: string
  tag_name: string
  preference_value: PreferenceValue
  job_value: string | null
  evidence: string | null
  status: AssessmentStatus
  score: number | null
  detail: string
}

export interface QualificationHint {
  tag_id: string
  requirement: string | number | null
  current: string | number | null
  status: 'meets_requirement' | 'preparation_needed' | 'pending_confirmation' | 'reference_only'
  detail: string
}

export interface JobMatch {
  job_id: string
  name: string | null
  city: string | null
  direction: string | null
  score: number | null
  hard_condition_status: HardStatus
  hard_conditions: Assessment[]
  scoring_coverage: { scored_preferences: number; unknown_preferences: number }
  soft_scores: Assessment[]
  match_reasons: Assessment[]
  unmet_preferences: Assessment[]
  unknown_tags: string[]
  qualification_hints: QualificationHint[]
}

export interface FollowUpQuestion {
  tag_id: string
  tag_name: string
  text: string
  distinction_score: number
  known_jobs: number
  candidate_jobs: number
}

export interface ExplanationSection {
  text: string
  evidence: { source_id: string; quote: string }[]
}

export interface JobExplanation {
  job_id: string
  job_name: string
  overview: ExplanationSection
  responsibilities: ExplanationSection
  work_style: ExplanationSection
  qualifications: ExplanationSection
  missing_information: string[]
  generated_by: 'llm' | 'fallback'
}

export interface PlanningReport {
  session_id: string
  mode: string
  hard_preferences: { name: string; preference: Preference }[]
  soft_preferences: { name: string; preference: Preference }[]
  qualifications: Qualifications
  preference_changes: PreferenceChange[]
  pending_suggestions: Suggestion[]
  candidate_count: number
  recommended_jobs: JobMatch[]
  questions: FollowUpQuestion[]
  job_details_to_confirm: { job_id: string; tag_id: string; message: string }[]
  advice: string[]
}
