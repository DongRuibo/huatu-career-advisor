# AI 职业规划辅助系统接口手册

适用于本项目的 FastAPI 服务。默认地址为 `http://127.0.0.1:8000`，接口版本为 `0.1.0`。服务启动和 Excel 导入步骤见 [README](README.md#启动与演示)。

## 1. 如何在 Swagger 调用

1. 打开 [Swagger 操作页](http://127.0.0.1:8000/docs)，展开要测试的接口，点击 **Try it out**。
2. 路径参数填在对应输入框，查询参数按需填写。有请求体的接口在 **Request body** 输入 JSON。
3. 点击 **Execute**。在 **Server response** 查看实际 HTTP 状态和 **Response body**。下面的 **Example Value** 是文档示例。
4. 咨询流程从 `POST /sessions` 开始。复制响应中的 `session_id`，后续会话接口都用它。修改画像后，单独调用 `GET /sessions/{session_id}/matches` 获取重新计算的结果。

| 常用 ID | 哪里找到 | 怎么用 |
|---|---|---|
| `session_id` | 创建会话的响应 | UUID，指定要操作的咨询会话 |
| `turn_id`、`turn_no` | 提交发言的响应 | `turn_id` 是数据库中的发言 ID。 `turn_no` 是该会话从 1 开始的第几轮 |
| `evidence_turn_id` | 偏好、待确认建议或修正记录 | 对应原话的 `turn_id`。确认建议时直接复制该建议的这个字段 |
| `tag_id` | 画像、岗位标签、匹配明细。完整词典见 [测试工作簿](data/测试数据.xlsx) 的「标签字典」工作表 | 如 `T001` 表示意向地区，`T003` 表示出差频率 |
| `job_id` | 岗位列表或匹配结果 | 如 `P012`，用于查岗位详情或生成讲解 |
| `run_id` | Excel 导入输出或最近一次导入报告 | UUID，指定要查询的导入批次 |

除 Markdown 报告外，以下业务接口的成功响应均为 JSON。带 JSON 请求体的调用使用 `Content-Type: application/json`。本地服务可直接通过 Swagger 操作。

## 2. 接口一览

| 方法 | 路径 | 用途 | 成功状态 |
|---|---|---|---|
| POST | `/sessions` | 创建咨询会话 | 201 |
| POST | `/sessions/{session_id}/turns` | 提交一轮发言并增量更新画像 | 201 |
| GET | `/sessions/{session_id}/preferences` | 查看画像、原话依据和修正记录 | 200 |
| PUT | `/sessions/{session_id}/preferences/{tag_id}` | 手动设置偏好或确认建议 | 200 |
| GET | `/sessions/{session_id}/matches` | 查询候选岗位及评分明细 | 200 |
| GET | `/sessions/{session_id}/jobs/{job_id}/explain` | 生成带来源引用的岗位讲解 | 200 |
| GET | `/sessions/{session_id}/questions` | 生成智能追问 | 200 |
| GET | `/sessions/{session_id}/report` | 生成 JSON 或 Markdown 规划报告 | 200 |
| GET | `/jobs` | 按城市、方向、标签筛选岗位 | 200 |
| GET | `/jobs/{job_id}` | 查询岗位字段、标签与知识资料 | 200 |
| POST | `/jobs` | 新增岗位 | 201 |
| PUT | `/jobs/{job_id}` | 修改岗位字段或替换业务标签 | 200 |
| GET | `/import/issues` | 查询 Excel 导入批次报告 | 200 |

FastAPI 另提供 [Swagger](http://127.0.0.1:8000/docs)、[ReDoc](http://127.0.0.1:8000/redoc)、[OpenAPI JSON](http://127.0.0.1:8000/openapi.json) 和 Swagger 内部使用的 `GET /docs/oauth2-redirect`。

## 3. 会话、发言与画像

### POST `/sessions`

创建一个独立会话。无参数，无请求体。返回 **201**。

```json
{
  "session_id": "860db8f5-6a01-4811-8e17-35d90a665acb",
  "mode": "future_planning"
}
```

`mode` 固定为 `future_planning`，即未来职业规划。上面的 UUID 仅作示例，实际调用使用自己创建的会话 ID。

### POST `/sessions/{session_id}/turns`

必填路径参数 `session_id`。请求体要求 `speaker` 和非空 `text`。角色只接受「家长」「学生」「咨询师」。

```json
{
  "speaker": "家长",
  "text": "我们只考虑成都市的体制内岗位，尽量稳定，不希望经常出差。"
}
```

返回 **201**。先校验模型提取结果，再写入该轮发言、有效偏好和资格。咨询师发言只保存原话，该轮的 `changes` 为空。

| 响应字段 | 含义 |
|---|---|
| `turn_id` | 本轮发言的数据库 ID，整数 |
| `turn_no` | 在当前会话中的顺序，从 1 开始 |
| `changes` | 本轮生效的偏好变更列表，结构见下方「变更记录」 |
| `qualifications` | 本轮提取到的资格字段，可能是空对象 `{}` |
| `pending_suggestions` | 本轮产生的待确认建议列表 |

会话不存在返回 **404**，角色或文本等请求参数有误返回 **422**。模型配置缺失返回 **503**。模型请求或整体响应校验失败返回 **502**，该轮发言及画像更新均未写入。重新提交成功会正式记录一轮，连续重复提交已成功的发言可能产生新轮次。

### GET `/sessions/{session_id}/preferences`

必填路径参数 `session_id`。返回 **200**，会话不存在返回 **404**。

| 响应字段 | 含义 |
|---|---|
| `preferences` | 已生效的偏好对象，以 `tag_id` 为键，如 `preferences.T001` |
| `qualifications` | 当前已知的资格，可能包含 `education`（学历）、`political_status`（政治面貌）、`experience_years`（经验年数）。未提供的字段省略 |
| `changes` | 正式偏好的变更记录列表 |
| `pending_suggestions` | 待确认建议，以 `tag_id` 为键。确认前不参与匹配评分 |

**正式偏好**举例，以下为 `preferences.T001` 的内容。

```json
{
  "tag_id": "T001",
  "value": ["成都"],
  "strength": "hard",
  "constraint_type": "in",
  "evidence_turn_id": 42,
  "evidence_text": "我们只考虑成都市的体制内岗位"
}
```

- `strength` 的 `hard` 表示硬条件，`soft` 表示软偏好。只有 T001 地区、T002 方向、T003 出差频率支持硬条件。
- `constraint_type` 可取 `eq`（等于）、`in`（集合中任选其一）、`max`（上限）、`min`（下限）、`prefer`（倾向）。每个标签支持的组合由标签字典与业务规则校验。
- `value` 可为单个字符串、字符串数组，或加班复合值 `{"preferred":"少","acceptable_max":"偶尔"}`。显式确认的避开建议使用 `{"avoid":"办公室","needs_confirmation":false}`。
- `evidence_text` 是用于抽取的发言片段。手动设置的偏好，其原话和对应发言 ID 为 `null`。

**变更记录**包含 `tag_id`、`old_value`、`new_value`、`reason`、`evidence_turn_id`、`turn_no`。例如 `old_value: null` 表示此前未设置，`new_value: ["成都"]` 表示新值为成都。手动修改的来源轮次可以为空。此结构记录新旧取值，当前强度看 `preferences` 里的 `strength`。

**待确认建议**包含 `tag_id`、`value`、`evidence_turn_id`、`evidence_text`。例如 T007 的 `value` 为 `{"avoid":"办公室","needs_confirmation":true}`。提交发言返回建议数组，读取画像返回按标签索引的建议对象。

### PUT `/sessions/{session_id}/preferences/{tag_id}`

必填路径参数 `session_id`、`tag_id`。请求体通过 `action` 选择操作。两种操作都返回 **200** 和完整画像。

**设置偏好**。在 Swagger 的 `tag_id` 填 `T001`，以下请求把成都、重庆都纳入硬条件的允许城市。

```json
{
  "action": "set",
  "value": ["成都", "重庆"],
  "strength": "hard",
  "constraint_type": "in"
}
```

`action`、`value`、`strength`、`constraint_type` 均必填。T001/T002 使用字符串数组，设为硬条件时用 `in`。T003 设为出差上限时使用 `{"action":"set","value":"偶尔","strength":"hard","constraint_type":"max"}`。参数或标签取值无效返回 **422**。同值同强度同约束的重复设置不会新增正式变更记录。

**确认建议**。例如在 `tag_id` 填 `T007`，将 `42` 替换为这条建议实际返回的 `evidence_turn_id`。该值是大于 0 的整数。

```json
{
  "action": "confirm",
  "evidence_turn_id": 42
}
```

服务验证建议所属会话、来源发言和当前状态。确认后建议变为正式软偏好，并从待确认列表移除。建议不存在、发言 ID 不匹配或重复确认返回 **404**。两种操作都要求会话已存在。

## 4. 匹配、讲解、追问与报告

### GET `/sessions/{session_id}/matches`

必填路径参数 `session_id`。返回 **200** 和全部候选岗位数组，无候选时返回 `[]`。会话不存在返回 **404**。此接口执行规则筛选和评分。

| 响应字段 | 含义 |
|---|---|
| `job_id`、`name`、`city`、`direction` | 岗位 ID、名称、城市和职业方向 |
| `score` | 软偏好得分，范围 0–1，是已知软偏好项的平均分。没有可评分项时为 `null` |
| `hard_condition_status` | `confirmed` 表示已设置的硬条件均已满足，或当前无硬条件。 `pending_confirmation` 表示至少一项硬条件缺资料，岗位暂保留 |
| `hard_conditions`、`soft_scores` | 硬条件核对、软偏好评分明细 |
| `scoring_coverage` | `scored_preferences` 为参与平均的偏好数，`unknown_preferences` 为因岗位资料未知而跳过的软偏好数 |
| `match_reasons` | 已满足的硬条件，以及满足或部分满足的软偏好 |
| `unmet_preferences` | 已知但不满足的软偏好明细 |
| `unknown_tags` | 因岗位资料缺失、需要确认的相关标签 ID |
| `qualification_hints` | 资格提示数组。每项包含 `tag_id`、`requirement`、`current`、`status` 和 `detail` |

匹配明细中的 `tag_id`、`tag_name` 标识评估维度，`preference_value` 是用户偏好，`job_value` 是岗位实际取值，`evidence` 是岗位事实依据。每条还有 `status`、`score`、`detail` 三个字段。硬条件明细与未知项的单项评分可以是 `null`。

- **偏好明细状态**有 `confirmed`（硬条件满足）、`pending_confirmation`（待确认）、`matched`（软偏好满足）、`partially_matched`（部分满足）、`unmet`（未满足）。
- **资格提示状态**有 `meets_requirement`（满足参考要求）、`preparation_needed`（学历或经验差距可提前准备）、`pending_confirmation`（资料或对应关系待确认）、`reference_only`（专业要求或已知的政治面貌差异）。政治面貌差异需按实际招聘公告核实。资格提示不参与候选筛选或评分。
- **排序**先展示 `confirmed`，再展示 `pending_confirmation`。各组按软偏好分数降序排列，无分数的岗位排在该组最后，同分按岗位 ID 升序排列。
- **读分示例**。S-A 特定画像下 P012 的稳定性为 1 分、群众接触为 0.5 分，收入未知。结果为 `score: 0.75`，评分覆盖为 2 项已评分、1 项未知。未知项在平均时跳过，已知不满足的 0 分计入平均。同分按岗位 ID 排序。

### GET `/sessions/{session_id}/jobs/{job_id}/explain`

必填路径参数 `session_id`、`job_id`。例如 `job_id` 填 `P012`。返回 **200**。会话或岗位不存在返回 **404**。

| 响应字段 | 含义 |
|---|---|
| `job_id`、`job_name` | 被讲解的岗位 |
| `overview`、`responsibilities`、`work_style`、`qualifications` | 岗位概况、职责、工作方式、资格参考。每段的 `text` 是通俗讲解，`evidence` 是对应的原文引用 |
| `evidence[]` | 单段的引用列表，每条含 `source_id` 和原文 `quote`。如 `job.description` 表示岗位描述，`tag.T003` 表示出差标签的取值或事实依据，`doc.<资料ID>` 表示知识资料 |
| `missing_information` | 缺少资料的事项，如「收入相对水平：资料未提供」 |
| `generated_by` | `llm` 表示至少一段采用了通过校验的模型改写，`fallback` 表示各段均根据岗位资料整理或显示缺失提示 |

接口读取指定岗位的字段、标签与知识资料。模型用对应引用支持的自然短句组织讲解。程序逐段核对结构、原文引用和正文中的事实。校验失败的段落根据岗位资料整理，缺少可用资料时显示「资料未提供」。

### GET `/sessions/{session_id}/questions`

必填路径参数 `session_id`。返回 **200** 和最多两条问题的数组，无合适问题时返回 `[]`。会话不存在返回 **404**。

| 每条问题的字段 | 含义 |
|---|---|
| `tag_id`、`tag_name` | 被选择的偏好标签和名称 |
| `text` | 建议咨询师提出的问题 |
| `distinction_score` | 当前候选在该标签上的区分度，越高越优先 |
| `known_jobs`、`candidate_jobs` | 该标签有有效资料的候选数、候选总数 |

代码从未生效的偏好里选择能区分候选的标签，并按该标签的取值或待确认建议生成问句。此调用只返回提问建议，提交学生或家长的回答才会触发新一轮抽取。

### GET `/sessions/{session_id}/report`

必填路径参数 `session_id`。可选查询参数如下。

| 查询参数 | 默认值 | 取值说明 |
|---|---|---|
| `format` | `json` | `json` 返回 `application/json`。 `md` 返回 `text/markdown` |
| `top_n` | `5` | 大于 0 的整数，限制报告推荐清单长度，候选总数仍完整统计 |

返回 **200**。会话不存在返回 **404**，格式或数量超出允许范围返回 **422**。示例查询参数 `?format=md&top_n=2`。

| JSON 响应字段 | 含义 |
|---|---|
| `session_id`、`mode` | 会话 UUID 和未来规划模式 |
| `hard_preferences`、`soft_preferences` | 硬条件和软偏好列表。每项含标签名 `name` 和偏好对象 `preference` |
| `qualifications`、`preference_changes` | 当前资格与偏好修正记录 |
| `pending_suggestions` | 仍待显式确认的建议列表 |
| `candidate_count` | 完整候选岗位数量 |
| `recommended_jobs` | 匹配结果的前 `top_n` 项，结构与匹配结果一致 |
| `questions` | 基于完整候选生成的追问列表 |
| `job_details_to_confirm` | 推荐清单里需要核实的岗位信息，每项含 `job_id`、`tag_id`、`message` |
| `advice` | 职业规划建议文本列表 |

Markdown 展示职业意向概览、岗位匹配概况、推荐岗位和主要依据、待确认问题及职业规划建议。评分明细、原话证据和偏好修改历史可通过 JSON 查看。模型从程序提供的建议中选择和排序，失败时沿用规则顺序。政治面貌差异出现在岗位资格参考中。

## 5. 岗位查询与维护

### GET `/jobs`

三个查询参数都可省略，传入多个时同时满足。返回 **200** 和按 `job_id` 排序的岗位数组。

| 参数 | 示例 | 含义 |
|---|---|---|
| `city` | `成都` | 精确匹配工作城市 |
| `direction` | `体制内` | 精确匹配工作簿的「所属方向」，对应原题的行业筛选 |
| `tag` | `T005:多` | 精确匹配一项岗位标签，格式为「标签 ID:标签值」 |

模拟工作簿缺少独立的行业分类字段。接入真实行业数据后，可新增独立的 `industry` 字段和查询条件，继续用 `direction` 筛选所属方向。

岗位结果中的 `is_matchable` 表示是否进入匹配，这个列表也可查到标记为 `false` 的岗位。无命中时返回 `[]`。标签参数缺少冒号、ID 或值时返回 **422**。

```bash
curl -fsS -G 'http://127.0.0.1:8000/jobs' \
  --data-urlencode 'city=成都' \
  --data-urlencode 'direction=体制内' \
  --data-urlencode 'tag=T005:多'
```

### GET `/jobs/{job_id}`

必填路径参数 `job_id`，例如 `P012`。返回 **200**，找不到岗位返回 **404**。

| 响应字段 | 含义 |
|---|---|
| `job` | 岗位对象，含业务字段、`is_matchable`、`created_at` 和 `modified_at` |
| `tags` | 岗位标签数组，每项含 `tag_id`、`name`、`value`、`evidence` |
| `documents` | 该岗位直接关联的知识资料数组，每项含 `doc_id`、`doc_type`、`title`、`content`、`usage_limit` |

`GET /jobs` 返回 `job` 这一层的字段列表。单个岗位详情还带标签和资料。

### POST `/jobs`

接收岗位 JSON。必填 `job_id`，其他字段可按需提供。岗位要参与匹配，需具备有效的 `name`、`city`、`direction`。返回 **201** 和完整岗位详情，ID 已存在返回 **409**，不合法的数据返回 **422**。

```json
{
  "job_id": "API-DEMO-001",
  "name": "接口演示岗位",
  "city": "成都",
  "direction": "体制内",
  "tags": {
    "T005": {"value": "多", "evidence": "示例岗位需要面向群众答疑。"}
  }
}
```

示例会向当前数据库新增模拟岗位。测试增改时，使用测试库或独立测试数据，以免影响演示场景的候选数。

### PUT `/jobs/{job_id}`

必填路径参数 `job_id`。传入要改的岗位字段。例如把前一节新增示例岗位的城市改为重庆。省略 `tags` 时保留现有业务标签。

```json
{
  "city": "重庆",
  "remark": "接口修改示例"
}
```

返回 **200** 和完整岗位详情。岗位不存在返回 **404**，非法字段或值返回 **422**。可空字段可用 `null` 清除。传入 `tags` 时会整体替换原有的 T003–T016，传 `"tags": {}` 会清空这组标签。岗位字段和标签变更在同一事务中完成。

| 新增与修改共用字段 | 写入规则 |
|---|---|
| `name`、`city`、`direction` | 岗位名称、工作城市、职业方向。城市和方向的非空值需符合标签字典 |
| `unit_type`、`unit_name`、`description`、`remark` | 单位类型、单位名称、工作内容、备注 |
| `education_req`、`major_req`、`political_req`、`experience_years` | 学历、专业、政治面貌、经验年数要求。非空值需符合标签字典，年数是非负整数 |
| `updated_at` | 岗位资料的更新时间，可传 ISO 8601 日期时间字符串。 `created_at` 和 `modified_at` 由数据库维护 |
| `tags` | 对象，以 T003–T016 为键。每项的 `value` 必填， `evidence` 可选。地区 T001、方向 T002、资格 T017–T020 从岗位字段同步 |
| `job_id` | 新增时必填，在修改时由路径指定 |
| `is_matchable` | 只读，随名称、城市、方向是否齐全自动计算 |

## 6. 导入批次

### GET `/import/issues`

可选查询参数 `run_id` 为 UUID。留空查最近一次成功导入的报告，填写则查指定批次。返回 **200**，没有可用批次或 ID 找不到时返回 **404**，UUID 格式错误返回 **422**。导入由 `python -m app.etl` 执行，此接口用于查询结果。

| 响应字段 | 含义 |
|---|---|
| `run_id` | 导入批次 ID |
| `total_rows`、`success_count` | 原始岗位行数、该批次成功入库的岗位数。成功入库的记录可能包含不可匹配岗位 |
| `created_at` | 批次记录时间 |
| `issue_counts` | 按异常类型汇总的数量 |
| `issues` | 问题详情数组，每项含 `job_id`、`issue_type`、`detail`、`created_at`，来源未关联到岗位时 `job_id` 可为空 |

内置模拟 Excel 的验收基线为 **22 行、21 个入库岗位**。问题类型包括 `TRIMMED_WHITESPACE`（去除首尾空格）、`DUPLICATE`（重复）、`CONFLICT`（冲突已按更新时间解决）、`CONFLICT_UNRESOLVED`（冲突无法判定）、`MISSING_KEY_FIELD`（关键字段缺失）、`MISSING_TAG`（缺少有效标签）、`INVALID_VALUE`（值或关联校验失败）。

## 7. 常见错误与排查顺序

| HTTP 状态 | 在哪里常见 | 怎么查 |
|---|---|---|
| 404 | 会话、岗位、待确认建议或导入批次不存在 | 核对路径 ID。确认建议时使用当前会话仍待确认的那一条 |
| 409 | 创建已有 ID 的岗位 | 使用新 ID 创建，或用修改接口更新已有岗位 |
| 422 | 请求参数、请求体、标签值或约束组合有误 | 查看 `detail`。FastAPI 参数校验通常返回数组，`loc` 指位置、`msg` 指原因。业务校验也可能返回字符串 |
| 502 | 提交家长或学生发言时抽取失败 | 当前一轮未写入。检查模型接口和后端配置，可重试原发言。此状态合并了上游请求错误和响应校验失败 |
| 503 | 提交发言时模型配置不完整 | 在后端配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，修改后重新启动服务 |
| 500 | 运行时或数据库操作异常 | 查看后端终端。重新提交修改前可先查询当前记录，核对是否已有变更 |

常规业务错误示例。

```json
{"detail":"会话不存在"}
```

面试时可按「创建会话 → 提交前 4 轮 S-A → 查询画像和匹配 → 提交第 5 轮 → 再查匹配 → 讲解、追问、报告」顺序演示。更完整的逐轮输入见 [Swagger 演示指南](DEMO_GUIDE.md)。接口定义位于 [app/main.py](app/main.py)。
