# AI 职业规划辅助系统

基于模拟咨询对话抽取职业偏好，按明确的硬条件和软偏好匹配模拟岗位，并提供岗位讲解、待确认问题和职业规划报告。当前学历、工作经验和政治面貌用于资格参考。

## 启动与演示

在项目根目录执行。后端使用 Python 3.12 和 `uv`，PostgreSQL 16 由 Docker Compose 启动。

1. 首次配置时执行 `cp .env.example .env`，在 `.env` 设置 `POSTGRES_PASSWORD`、`LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`。模型地址支持 `/v1` 基地址和完整 `/chat/completions` 地址。
2. 启动数据库、导入工作簿并启动接口。

   ```bash
   uv sync --locked
   docker compose up -d --wait db
   uv run --locked --env-file .env python -m app.etl
   uv run --locked --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

3. 在 Swagger 打开 [接口文档](http://127.0.0.1:8000/docs)。另开终端运行演示，并查看三个场景的画像、候选数、讲解、追问和报告摘要。

   ```bash
   uv run --locked python -m scripts.demo --base-url http://127.0.0.1:8000
   ```

已有阶段二数据卷时，先启动数据库，再执行以下命令升级表结构并迁移旧建议。新数据卷首次初始化会自动执行建表脚本。

```bash
docker compose exec -T db sh -c 'psql -1 -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/schema.sql
```

## Vue 咨询工作台

先按上面的步骤启动 FastAPI。另开一个终端，在项目根目录执行以下命令。前端需要 Node.js 20.19+（20.x）或 22.12+。

```bash
cd frontend
npm ci
npm run dev
```

打开 [咨询工作台](http://127.0.0.1:5173/)。Vite 默认将 `/api` 请求转发到 `http://127.0.0.1:8000`。后端使用其他地址时，可以用 `VITE_BACKEND_URL=http://127.0.0.1:8001 npm run dev` 启动前端。模型配置仍从后端的 `.env` 读取。

1. 点击 **新建会话**，选择演示场景 S-A、S-B 或 S-C。点击 **填入下一轮**，检查角色及原话后点击 **提交发言**。也可输入自己的发言。
2. 观察右侧画像及岗位列表。S-A 在接受重庆时，验收基线从 6 个候选增加到 11 个。S-B 有 17 个候选，可显式确认办公方式或工作内容建议。S-C 的基线从 4 个候选增加到 11 个，可查看学历与经验的准备提示，以及 P019 的政治面貌资格参考。发言由配置的模型抽取，实际结果以接口响应为准。
3. 在画像中使用编辑按钮修改城市或出差频率，保存后自动刷新。点击岗位可查看评分规则产生的明细、未知值及资格提示，切到 **岗位讲解** 后点击按钮生成资料说明。点击 **生成追问** 查看当前候选中可进一步确认的偏好。
4. 在 **职业规划报告** 填写推荐数量，点击 **生成报告** 查看并复制用户版 Markdown。报告包含职业意向概览、岗位匹配概况、推荐岗位和主要依据、待确认问题及职业规划建议。切到 **审计明细（JSON）** 可独立生成和复制含评分、原话证据和偏好修改历史的明细。

页面刷新后可新建会话继续演示。岗位维护和导入报告也可通过 [Swagger](http://127.0.0.1:8000/docs) 操作。前端类型检查与构建命令为 `npm run typecheck` 和 `npm run build`，均在前端目录运行。

### 岗位管理

1. 点击顶部 **岗位管理**。选择工作城市 **重庆**、职业方向 **体制内**、岗位标签 **群众接触程度**，输入取值 **多**，点击 **查询岗位**。三个条件同时生效。点击岗位名称可查看字段、标签依据和知识资料。
2. 点击 **新增岗位** 或在详情中点击 **编辑岗位**。保存成功后，详情和当前筛选结果会刷新。只修改岗位字段时会保留已有业务标签。
3. 切回 **咨询工作台** 可以继续原会话。岗位资料发生修改后，当前会话会重新读取匹配结果。

新增或修改会写入当前数据库。演示增改时可用独立测试库。如果同时要核对 S-A、S-B、S-C 的固定候选数量，也可在新增演示岗位时留空一个匹配必填字段，让该条记录标为「资料待补」。

考核题中的行业筛选采用模拟工作簿的「所属方向」，通过 `GET /jobs?direction=...` 筛选体制内、央国企和大型私企等分类。工作簿缺少独立的行业字段。接入真实行业数据后，可增设独立的 `industry` 字段和查询参数，继续保留 `direction` 表达职业方向。

## Swagger 流程

在每个接口点击 **Try it out**。`session_id` 使用创建会话获得的 UUID。

| 顺序 | 方法与路径 | 示例输入或操作 |
|---|---|---|
| 1 | `POST /sessions` | 创建 `future_planning` 会话，复制 `session_id` |
| 2 | `POST /sessions/{session_id}/turns` | `{"speaker":"家长","text":"我们只考虑成都市的体制内岗位，尽量稳定，不希望经常出差。"}` |
| 3 | `POST /sessions/{session_id}/turns` | `{"speaker":"家长","text":"重庆也可以考虑"}`，查看返回的修正记录 |
| 4 | `GET /sessions/{session_id}/preferences` | 查看正式画像、待确认建议、资格和修正记录 |
| 5 | `GET /sessions/{session_id}/matches` | 查看得分、覆盖统计、匹配依据与资格差距 |
| 6 | `GET /sessions/{session_id}/jobs/{job_id}/explain` | 使用 `P012` 查看岗位讲解与缺失说明 |
| 7 | `GET /sessions/{session_id}/questions` | 查看最多两条未明确偏好的追问 |
| 8 | `GET /sessions/{session_id}/report` | `format=md&top_n=5` 返回 Markdown，`format=json` 返回 JSON |

`PUT /sessions/{session_id}/preferences/{tag_id}` 使用以下两种请求体。确认建议时使用该建议返回的 `evidence_turn_id`。确认后重新调用匹配接口。

```json
{"action":"set","value":["成都","重庆"],"strength":"hard","constraint_type":"in"}
```

```json
{"action":"confirm","evidence_turn_id":42}
```

### curl 示例

```bash
base=http://127.0.0.1:8000
curl -sS -X POST "$base/sessions"
# 替换为创建会话返回的 session_id
sid=替换成会话UUID
curl -sS -X POST "$base/sessions/$sid/turns" \
  -H 'Content-Type: application/json' \
  -d '{"speaker":"家长","text":"只考虑成都的体制内岗位，完全不能出差。"}'
curl -sS "$base/sessions/$sid/preferences"
curl -sS "$base/sessions/$sid/matches"
curl -sS "$base/sessions/$sid/jobs/P012/explain"
curl -sS "$base/sessions/$sid/questions"
curl -sS "$base/sessions/$sid/report?format=md&top_n=5"
curl -sS -G "$base/jobs" --data-urlencode 'city=成都' --data-urlencode 'direction=体制内' --data-urlencode 'tag=T005:多'
curl -sS "$base/import/issues"
```

`GET /jobs/{job_id}` 查询岗位详情。`POST /jobs` 增加岗位，`PUT /jobs/{job_id}` 修改传入字段。`tags` 可写 T003–T016，值形如 `{"T005":{"value":"多","evidence":"模拟事实依据"}}`。省略 `tags` 保留现有业务标签，传 `{}` 清空业务标签。T001、T002、T017–T020 自动同步，字段与标签在一个事务内保存。`GET /import/issues?run_id=...` 查询指定导入批次。

## 数据处理与匹配规则

- 文本首尾空白会清理并记录，P011 的城市归一为成都。相同岗位记录去重，P006 只保留一条。冲突版本优先取唯一最新版本，无法判断时隔离并记录 `CONFLICT_UNRESOLVED`。
- 岗位关键字段缺失会保留记录并关闭匹配，P021 缺城市。缺 ID 时按来源行和内容生成可重复的内部 ID。非法枚举转为未知。P012 缺 T008、P019 缺 T009、P020 缺 T011，导入报告也列出 P021 的缺失标签。标签缺失时保留未知。
- 每次导入在一个事务内执行。重跑同步本批岗位的标签，清除失效旧值并保留历史报告。给定表格共有 22 行岗位，入库 21 个唯一岗位。
- 匹配通过 Python 规则执行。硬条件按城市、方向、出差上限过滤，未知硬条件保留并标记待确认。软偏好对已知项取等权平均，返回已评分和未知项数。已确认岗位排在待确认岗位之前，各组按得分降序、无分数在后、同分按岗位 ID 排序。
- 学历、工作经验、政治面貌和专业只影响资格提示。学历和经验差距可用于准备，政治面貌差异提示核实实际招聘公告，专业展示岗位原始参考要求。
- 模型提议由 Pydantic、字典、说话人和本轮原话核验。咨询师问题仅保存为发言。T007、T012 的间接推断先写待确认建议，显式确认才进入画像。
- 追问区分度为已知覆盖率乘以已知取值的基尼不纯度。程序按选中的标签及取值生成问句。没有可区分的标签时返回空列表。

## 验收命令

```bash
# 离线单元测试
HUATU_TEST_POSTGRES=0 HUATU_RUN_LIVE_LLM=0 uv run --locked python -m pytest -q

# 全量本地测试，数据库用例在临时独立 schema 中运行
HUATU_TEST_POSTGRES=1 HUATU_RUN_LIVE_LLM=0 uv run --locked --env-file .env python -m pytest -q -W error

# 单独运行真实模型验收
HUATU_TEST_POSTGRES=1 HUATU_RUN_LIVE_LLM=1 \
  uv run --locked --env-file .env python -m pytest -q -s tests/test_live_llm.py
```

固定响应测试覆盖非法标签、证据、角色、损坏响应和服务异常。数据库集成测试回放工作簿对话，验证 S-A 为 6 → 11，S-B 为 17，S-C 为 4 → 11。单独启用真实模型验收时，完整流程会发出 18 次 `/chat/completions` 请求（11 次抽取、3 次岗位讲解、4 次报告建议），并输出调用结果。运行前需确认服务凭据和计费。

## 简化范围与技术取舍

- 标签抽取由 LLM 提议，程序核对原话、标签和值。城市表达「希望」时按软偏好处理，明确说「只考虑」或「必须」时按硬条件处理。证据不足的提议会被跳过，整个抽取请求失败时本轮不入库。复杂表达仍可能出现语义误判。
- 岗位讲解由 LLM 选取来源并组织正文，程序逐段核对正文和引用并按资料回退。智能追问由规则选题并生成问句。报告建议由程序提供选项，LLM 选择和排序，失败时按规则顺序展示。
- 知识库按岗位 ID 管理资料，供岗位讲解和来源核对使用。
- 系统使用模拟岗位数据，Python 匹配面向小规模岗位集。报告默认取前 5 名。推荐供职业规划参考，报考资格以实际招聘公告为准，录用以实际招聘结果为准。
- PostgreSQL 和模型调用采用同步方式。高并发下需缩短对话提交的事务边界，并解决同一会话的 `MAX(turn_no)+1` 并发竞争。
- 异步任务与模型队列、规模化检索、向量检索或 RAG、微服务拆分、登录权限和生产部署监控属于后续生产化范围。

## 扩展方案

- **数百万岗位、数千标签**。先用 `job_tags(tag_id, value)` 倒排索引或位图执行硬条件，缩小候选后再按确定性规则评分。高并发多条件检索可交给 OpenSearch，常用标签可做宽表物化，批量预计算稳定特征并缓存热门画像结果。随数据量再考虑分库分表与读写分离。
- **实时语音转写**。用 WebSocket 接入流式 ASR，按说话人分离并识别家长、学生、咨询师。在句子边界形成完整发言，复用 `/turns` 入口，将抽取异步化，继续增量更新画像和原话证据。
