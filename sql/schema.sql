-- PostgreSQL 建表和升级脚本，覆盖岗位资料、标签字典、咨询画像和导入记录。
-- 主键、外键、唯一约束和索引支持岗位维护、会话追溯与条件筛选。
-- 脚本可重复执行，并兼容把旧版画像中的待确认建议迁移到独立表。

CREATE TABLE IF NOT EXISTS jobs (
    job_id text PRIMARY KEY,
    name text,
    direction text,
    city text,
    unit_type text,
    unit_name text,
    description text,
    education_req text,
    major_req text,
    political_req text,
    experience_years integer,
    updated_at timestamp,
    is_matchable boolean NOT NULL,
    remark text,
    created_at timestamptz NOT NULL DEFAULT now(),
    modified_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tag_definitions (
    tag_id text PRIMARY KEY,
    name text NOT NULL,
    category text NOT NULL,
    data_type text NOT NULL,
    value_mode text NOT NULL CHECK (value_mode IN ('单选', '多选')),
    allowed_values text[] NOT NULL,
    ordered boolean NOT NULL,
    match_rule text NOT NULL CHECK (match_rule IN ('硬条件', '偏好排序', '资格参考')),
    source text NOT NULL,
    unknown_rule text NOT NULL
);

-- 字典定义标签规则，关联表保存各岗位的实际标签值和事实依据。
CREATE TABLE IF NOT EXISTS job_tags (
    job_id text REFERENCES jobs (job_id),
    tag_id text REFERENCES tag_definitions (tag_id),
    value text NOT NULL,
    evidence text,
    PRIMARY KEY (job_id, tag_id)
);

CREATE TABLE IF NOT EXISTS knowledge_docs (
    doc_id text PRIMARY KEY,
    job_id text REFERENCES jobs (job_id),
    doc_type text NOT NULL,
    title text NOT NULL,
    content text NOT NULL,
    usage_limit text
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mode text NOT NULL DEFAULT 'future_planning' CHECK (mode = 'future_planning'),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- id 用于证据外键，turn_no 是会话内展示的顺序，同一会话中不能重复。
CREATE TABLE IF NOT EXISTS dialogue_turns (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES sessions (session_id),
    turn_no integer NOT NULL,
    speaker text NOT NULL CHECK (speaker IN ('家长', '学生', '咨询师')),
    text text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, turn_no)
);

-- 每个会话每种标签只保留一份正式偏好，用 JSONB 表示单值、多选和复合值。
CREATE TABLE IF NOT EXISTS user_preferences (
    session_id uuid REFERENCES sessions (session_id),
    tag_id text REFERENCES tag_definitions (tag_id),
    value jsonb NOT NULL,
    strength text NOT NULL CHECK (strength IN ('hard', 'soft')),
    constraint_type text NOT NULL CHECK (constraint_type IN ('eq', 'in', 'max', 'min', 'prefer')),
    evidence_turn_id bigint REFERENCES dialogue_turns (id),
    evidence_text text,
    PRIMARY KEY (session_id, tag_id)
);

-- 待确认建议单独存放，避免匹配引擎提前使用间接推断。
CREATE TABLE IF NOT EXISTS preference_suggestions (
    session_id uuid REFERENCES sessions (session_id),
    tag_id text REFERENCES tag_definitions (tag_id),
    value jsonb NOT NULL,
    evidence_turn_id bigint REFERENCES dialogue_turns (id),
    evidence_text text,
    PRIMARY KEY (session_id, tag_id)
);

-- 现有数据库升级时，将阶段二写入画像的待确认建议移到建议表。
WITH pending AS (
    DELETE FROM user_preferences
    WHERE tag_id IN ('T007', 'T012') AND value @> '{"needs_confirmation": true}'::jsonb
    RETURNING session_id, tag_id, value, evidence_turn_id, evidence_text
)
INSERT INTO preference_suggestions (session_id, tag_id, value, evidence_turn_id, evidence_text)
SELECT session_id, tag_id, value, evidence_turn_id, evidence_text FROM pending
ON CONFLICT (session_id, tag_id) DO NOTHING;

-- 当前值留在正式画像表。新增、替换和撤销由历史表记录前后取值与依据。
CREATE TABLE IF NOT EXISTS preference_changes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES sessions (session_id),
    tag_id text NOT NULL REFERENCES tag_definitions (tag_id),
    old_value jsonb,
    new_value jsonb,
    reason text NOT NULL,
    evidence_turn_id bigint REFERENCES dialogue_turns (id),
    changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_qualifications (
    session_id uuid PRIMARY KEY REFERENCES sessions (session_id),
    education text,
    political_status text,
    experience_years integer
);

-- 批次与异常分开记录，零异常的导入也能按批次查询。
CREATE TABLE IF NOT EXISTS import_runs (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    total_rows integer NOT NULL,
    success_count integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS import_issues (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES import_runs (run_id),
    job_id text,
    issue_type text NOT NULL,
    detail text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS jobs_city_idx ON jobs (city);
CREATE INDEX IF NOT EXISTS jobs_direction_idx ON jobs (direction);
CREATE INDEX IF NOT EXISTS job_tags_tag_id_value_idx ON job_tags (tag_id, value);
CREATE INDEX IF NOT EXISTS import_issues_run_id_idx ON import_issues (run_id);
