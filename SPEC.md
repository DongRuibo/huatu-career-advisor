# AI 职业规划辅助系统 · 开发规格（交给编码 AI 执行）

> 来源：华图教育实操考核题（`考核题.docx`）+ 测试数据（`测试数据.xlsx`）。
> 原则：**规则可解释、模型不编造、数据问题显式报告**。请严格按本规格实现；遇到规格未覆盖的决策，先列出来询问，不要自行发挥。

---

## 0. 技术栈与约束

- Python 3.12，依赖用 `uv` 管理；FastAPI + Uvicorn；SQLAlchemy 2.x（或 psycopg 直接写 SQL）；Pandas + openpyxl；Pydantic v2。
- PostgreSQL 16，用 `docker compose` 启动（提供 `compose.yml`）。
- 大模型：OpenAI 兼容接口（DeepSeek / 通义千问 / 硅基流动均可），通过环境变量配置：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。**禁止把 Key 写进代码或提交**；提供 `.env.example`，`.gitignore` 忽略 `.env`。
- 不做前端、语音、向量数据库、PDF/Word。

## 1. 目录结构

```
huatu-career-advisor/
├── compose.yml              # PostgreSQL
├── pyproject.toml
├── .env.example
├── README.md                # 启动步骤、接口说明、简化范围、取舍、扩展方案（含加分题）
├── data/测试数据.xlsx
├── sql/schema.sql
├── app/
│   ├── main.py              # FastAPI 路由
│   ├── db.py
│   ├── etl.py               # 任务一：清洗 + 导入 + 异常报告
│   ├── dictionary.py        # 加载标签字典（允许值、有序枚举的顺序）
│   ├── extractor.py         # 任务二：LLM 抽取 + 宿主校验
│   ├── profile.py           # 任务二：多轮合并 + 修正记录
│   ├── matcher.py           # 任务三：确定性匹配引擎（不调用 LLM）
│   ├── advisor.py           # 任务四：岗位讲解 + 智能追问
│   ├── report.py            # 任务五：规划报告
│   └── llm.py               # OpenAI 兼容客户端封装
├── scripts/demo.py          # 依次跑 S-A / S-B / S-C 三个场景并打印结果（录屏用）
└── tests/                   # pytest：清洗、校验、合并、匹配规则（匹配测试不依赖 LLM）
```

## 2. 测试数据结构（6 个工作表）

| 工作表 | 内容 | 注意 |
|---|---|---|
| 岗位原始数据 | 22 行岗位：岗位ID、名称、所属方向、工作城市、单位类型、单位名称、工作内容、学历、专业、政治面貌、经验年限、更新时间、数据属性、备注 | 有脏数据，见 §3 |
| 标签字典 | 20 个标签 T001–T020：名称、分类、类型、单选/多选、允许值（`|` 分隔）、匹配规则分类、取值来源、未知值规则 | 表头在第 4 行，前 3 行是标题说明 |
| 岗位标签 | 长表：岗位ID、标签ID、标签值、事实依据 | 表头在第 4 行；部分岗位缺标签 |
| 知识库资料 | 每个岗位一段说明文字 + 使用限制 | 岗位讲解只能基于此 + 岗位表 |
| 模拟咨询对话 | 场景 S-A / S-B / S-C，多轮，含"对话背景备注"（=期望行为） | 用作验收用例 |
| 业务规则 | 10 条必须遵守的约定 | 本规格已吸收 |

标签来源：T001 意向地区 ← 岗位.工作城市；T002 职业方向 ← 岗位.所属方向；T017–T020 ← 岗位的学历/经验/政治面貌/专业字段；T003–T016 ← 岗位标签表。

## 3. 任务一：清洗与导入（etl.py）

读取时**按表头行定位**（标签字典、岗位标签的表头在第 4 行），不要写死行号以外的假设。

清洗规则（每一条处理都要写入 `import_issues` 表并在 README 说明策略）：
1. 所有文本字段去除首尾空格（如 P011 城市 `" 成都  "` → `成都`）。
2. **重复岗位**：P006 出现两次且内容一致 → 保留一条，记录 `DUPLICATE`。若同 ID 内容不一致 → 保留更新时间最新的一条并记录 `CONFLICT`。
3. **关键字段缺失**（岗位ID、名称、城市、方向任一为空）：如 P021 缺城市 → 仍入库但 `is_matchable = false`，记录 `MISSING_KEY_FIELD`，**不进入匹配**。
4. 城市、方向、标签值必须在字典允许值内，否则记录 `INVALID_VALUE`，该值视为未知。
5. 岗位缺少某个标签 → **不插入默认值**，查询时视为"未知"。（数据中 P012 缺 T008、P019 缺 T009、P020 缺 T011，请以程序检测结果为准。）
6. 导入幂等：重复运行不产生重复数据（upsert）。

导入完成后输出导入报告：总行数、成功数、各类问题明细。

## 4. 数据库设计（sql/schema.sql）

```sql
jobs(job_id PK, name, direction, city, unit_type, unit_name, description,
     education_req, major_req, political_req, experience_years, updated_at,
     is_matchable bool, remark, created_at, modified_at)

tag_definitions(tag_id PK, name, category, data_type, value_mode /*单选|多选*/,
     allowed_values text[], ordered bool, match_rule /*硬条件|偏好排序|资格参考*/,
     source, unknown_rule)

job_tags(job_id FK, tag_id FK, value, evidence, PRIMARY KEY(job_id, tag_id))
     -- T001/T002/T017-T020 也由岗位字段同步写入，便于统一查询

knowledge_docs(doc_id PK, job_id FK, doc_type, title, content, usage_limit)

sessions(session_id PK, mode /*future_planning*/, created_at)
dialogue_turns(id PK, session_id FK, turn_no, speaker /*家长|学生|咨询师*/, text, created_at)
user_preferences(session_id FK, tag_id FK, value jsonb /*多选为数组*/, strength /*hard|soft*/,
     constraint_type /*eq|in|max|min|prefer*/, evidence_turn_id, evidence_text,
     PRIMARY KEY(session_id, tag_id))
preference_changes(id PK, session_id, tag_id, old_value, new_value, reason, evidence_turn_id, changed_at)
user_qualifications(session_id, education, political_status, experience_years)  -- 仅作资格提示
import_issues(id PK, job_id, issue_type, detail, created_at)
```

索引：`jobs(city)`、`jobs(direction)`、`job_tags(tag_id, value)`。
岗位的新增 / 修改 / 查询接口 + 按城市、方向、标签条件筛选（见 §9）。

## 5. 任务二：标签抽取（extractor.py + profile.py）

**LLM 只负责"提议"，宿主负责"决定"。**

LLM 输入：本轮发言（含角色）+ 前几轮上下文 + 标签字典（名称、允许值、解释规则）。
LLM 输出（JSON，要求 `response_format=json_object` 或等价方式）：

```json
{"preferences": [
  {"tag_id": "T001", "value": ["成都"], "strength": "hard", "constraint_type": "in",
   "action": "set|add|remove", "evidence": "只考虑成都市", "speaker": "家长"}
 ],
 "qualifications": {"education": "本科", "political_status": "群众", "experience_years": 0}
}
```

宿主校验（**必须用代码实现，不能只靠提示词**）：
1. `tag_id` 必须存在；`value` 必须在该标签允许值内；否则丢弃并记录日志。
2. `evidence` 必须是本轮原话的子串（允许去空格比较），否则丢弃 → 防止模型凭空推断。
3. **speaker 为"咨询师"的发言不产生任何偏好**（咨询师提问 ≠ 家长意愿）。
4. `strength=hard` 只允许用于 T001、T002、T003；其他标签一律降为 `soft`。
5. 未提及的标签不出现在画像里（不得补默认值）。

提示词中必须写明的语义规则（来自标签字典与对话备注）：
- "不希望经常出差" → T003 上限 `偶尔`（max）；"完全不能出差" → 上限 `无`；"偶尔去现场没问题，但不希望经常跨城市出差" → T003 上限 `偶尔`。**外勤 ≠ 出差**。
- "只考虑 / 必须" → hard；"最好 / 更好 / 不是硬条件 / 尽量" → soft。
- "城市和职业方向都可以讨论" → **不设置** T001、T002。
- "加班最好少，偶尔可以接受" → T013 soft，偏好 `少`，可接受到 `偶尔`，不是禁止加班。
- "喜欢和群众沟通" → T005 soft 偏好 `多`（不是硬条件）。
- "不喜欢天天埋头整理资料" → 可推出 T007 偏好非纯办公室 / T012 不偏好材料管理，均为 soft，且要保留不确定性。
- "收入中上" → T008 soft 偏好 `中上`（有序，越接近越好，`较高`也可接受）。
- 学历、政治面貌、经验 → 只写入 `qualifications`，不成为偏好。

多轮合并（profile.py）：
- `action=add`（如"重庆也可以考虑"）→ T001 从 `["成都"]` 变为 `["成都","重庆"]`。
- `action=set` 覆盖旧值；`remove` 移除。
- 每次变化写入 `preference_changes`（旧值、新值、原话、轮次）。

## 6. 任务三：匹配引擎（matcher.py，纯规则，不调用 LLM）

输入：会话画像；输出：岗位列表（分数、匹配理由、未满足的偏好、未知标签、资格提示）。

1. **候选集**：`is_matchable = true` 的岗位。
2. **硬条件过滤**（只对画像中 strength=hard 的标签）：
   - T001 城市：岗位城市 ∈ 画像城市列表。
   - T002 方向：岗位方向 ∈ 画像方向列表。
   - T003 出差上限：按顺序 `无 < 偶尔 < 频繁`，岗位值 ≤ 上限。岗位该标签**未知**时：不排除，标记"出差情况未知，需确认"。
3. **软偏好打分**（只排序，不剔除）：
   - 有序枚举：完全一致 1.0，相差一级 0.5，更差 0；"更好"方向视为满足（如偏好收入 `中上`、岗位 `较高` → 1.0）。
   - 岗位标签未知：该项不计分，列入"待确认"。
   - 总分 = 各软偏好得分平均（可加权，权重写在配置里并在 README 说明）。
4. **资格提示**（T017–T020，不剔除）：比较用户当前学历 / 政治面貌 / 经验与岗位要求，生成提示文字，如"该岗位参考要求硕士，当前为本科，可作为长期准备方向"。
5. **匹配理由**：逐条列出满足的硬条件、命中的软偏好（附岗位标签值与事实依据）。
6. 修改偏好后重新调用即可重新匹配。

## 7. 任务四：AI 辅助咨询（advisor.py）

- **岗位讲解**：上下文只放该岗位的岗位表字段 + 标签（含事实依据）+ 知识库正文 + 使用限制。提示词要求：只依据资料、通俗易懂、资料没有的内容写"资料未提供"；**禁止输出工资金额、编制待遇、晋升概率、竞争比**。生成后用代码做一次检查：若出现金额 / 百分比 / "晋升概率"等词则重写或替换为提示语。
- **智能追问**：找出画像中仍未明确、且对当前候选岗位区分度最高的标签（例如候选岗位在该标签上取值差异最大），生成 1–2 个咨询师可以问的问题。区分度计算用代码，问题措辞可用 LLM。

## 8. 任务五：规划报告（report.py）

Markdown 和 JSON 两种格式，包含：
1. 用户职业意向与已识别标签（区分硬条件 / 软偏好，附原话证据与修正记录）
2. 推荐岗位清单（Top N）
3. 每个岗位的主要匹配依据
4. 需要进一步确认的问题（来自智能追问 + 未知标签）
5. 简短职业规划建议（LLM 生成，同样受事实边界约束；含资格准备提示）

## 9. 接口（FastAPI）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /sessions | 新建会话 |
| POST | /sessions/{id}/turns | 提交一轮对话 `{speaker, text}`，返回本轮新增 / 修改的标签 |
| GET | /sessions/{id}/preferences | 当前标签 + 修正记录 |
| PUT | /sessions/{id}/preferences/{tag_id} | 手动修改偏好（演示重新匹配） |
| GET | /sessions/{id}/matches | 匹配结果 |
| GET | /sessions/{id}/jobs/{job_id}/explain | 岗位讲解 |
| GET | /sessions/{id}/questions | 智能追问 |
| GET | /sessions/{id}/report?format=md\|json | 规划报告 |
| GET/POST/PUT | /jobs、/jobs/{id} | 岗位新增 / 修改 / 查询，支持 `?city=&direction=&tag=T005:多` 筛选 |
| GET | /import/issues | 数据导入异常报告 |

## 10. 验收用例（scripts/demo.py + tests）

| 场景 | 期望 |
|---|---|
| **导入** | 21 个唯一岗位；P006 去重；P011 城市修正为成都；P021 不可匹配并报告；缺失标签被检测出来 |
| **S-A** | hard：城市=成都、方向=体制内、出差上限=偶尔；soft：稳定性高、群众接触多、收入中上。结果不含成都以外、非体制内、出差频繁的岗位（如 P003、P016 被排除），不含 P021 |
| **S-A 追加一轮**"重庆也可以考虑" | 城市变为 [成都, 重庆]，有修正记录，重庆体制内岗位进入结果 |
| **S-B** | 不设置城市和方向；咨询师的提问不产生标签；出差上限=偶尔（hard）；节奏、加班、群众接触、办公方式均为 soft；加班偏好不排除"偶尔加班"的岗位 |
| **S-C** | hard：成都、体制内、出差上限=无；qualifications=本科/群众/0 年，**只产生资格提示，不排除任何岗位** |
| **讲解** | 不出现工资金额、晋升概率；资料缺失时明确说明 |

匹配引擎的单元测试直接构造画像，不依赖 LLM。

## 11. README 必须包含

启动步骤（docker compose → 导入 → 启动服务 → 运行 demo）、接口示例（curl）、数据问题与处理策略、**简化范围与技术取舍**、加分题：
- **扩展到数百万岗位、数千标签**：标签倒排索引（`job_tags(tag_id, value)` 或位图）、硬条件先走索引过滤再打分、ES / OpenSearch 承载多条件检索、标签宽表物化、批量预计算、缓存热门画像、分库分表与读写分离。
- **接入实时语音转写**：流式 ASR（WebSocket）→ 说话人分离（区分家长 / 学生 / 咨询师）→ 按句子边界切分后复用现有 `/turns` 接口 → 抽取异步化、画像增量更新。
