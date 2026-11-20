# Swagger 咨询流程演示

建议以 S-A 展示完整流程，再用 S-B 展示待确认建议，用 S-C 展示资格差距。三个场景分别创建会话。

## 启动与准备

1. 按 [README](README.md) 用 Python 3.12、uv 和 Docker Compose 启动 PostgreSQL、导入工作簿并启动 Uvicorn。首次使用时，在本地 `.env` 填写数据库口令和自己的 OpenAI 兼容 API 配置。
2. 打开 [Swagger](http://127.0.0.1:8000/docs)，在每个接口点 **Try it out**，填好参数后点 **Execute**。`POST /sessions` 返回的 `session_id` 用于该场景的所有后续请求。
3. 先执行 `GET /import/issues`。展示 22 行岗位来源、21 个成功入库的唯一岗位和 21 条导入问题。用 P006 重复、P011 城市清理、P021 缺城市、P012 缺收入标签解释处理策略。

## S-A 展示完整流程

### 建立画像

执行 `POST /sessions`，随后对 `POST /sessions/{session_id}/turns` 逐轮提交下表。每次请求包含 `speaker` 和 `text`。

| 轮次 | speaker | text | 展示结果 |
|---|---|---|---|
| 1 | 咨询师 | 你们希望孩子以后在哪个城市发展，主要考虑什么职业方向？ | 返回本轮编号，`changes` 为空。 |
| 2 | 家长 | 我们只考虑成都市的体制内岗位，尽量稳定，不希望经常出差。 | T001 成都、T002 体制内、T003 出差上限偶尔进入硬条件，T004 稳定性高进入软偏好。 |
| 3 | 学生 | 我比较喜欢和人沟通，尤其是面向群众做咨询、解释这类工作。 | T005 群众接触程度多进入软偏好。 |
| 4 | 家长 | 收入如果在当地算中上就更好，但这不是硬条件。 | T008 收入中上进入软偏好。 |

```json
{"speaker":"家长","text":"我们只考虑成都市的体制内岗位，尽量稳定，不希望经常出差。"}
```

调用 `GET /sessions/{session_id}/preferences` 展示 `preferences` 的 `strength`、`constraint_type`、`evidence_text`，以及 `changes` 的原话和轮次。

### 查询匹配并追加意向

1. 调用 `GET /sessions/{session_id}/matches`。最初 6 个候选，按岗位 ID 列为 P001、P005、P009、P011、P012、P018。选择 P012 展示 `score=0.75`。稳定性 1 分、群众接触 0.5 分，收入标签未知，`scoring_coverage` 为 2 项参与评分、1 项未知。
2. 新增第 5 轮 `{"speaker":"家长","text":"重庆也可以考虑"}`。查看 T001 修正记录，旧值 `["成都"]`，新值 `["成都","重庆"]`，硬条件强度保留。
3. 再查匹配，候选增加到 11 个。新增 P002、P008、P010、P017、P019。展开 `hard_conditions` 和 `match_reasons`，查看确认状态、岗位值和事实依据。

### 讲解、追问和报告

| 接口 | 操作与展示点 |
|---|---|
| `GET /sessions/{session_id}/jobs/{job_id}/explain` | `job_id=P012`。展示岗位概况、工作内容、工作方式、参考资格及证据来源。缺失项应包含「收入相对水平：资料未提供」。 |
| `GET /sessions/{session_id}/questions` | 查看一至两个针对尚未明确偏好的问题。展开 `distinction_score`、覆盖岗位数，说明标签由程序选择，措辞由模型生成。 |
| `GET /sessions/{session_id}/report` | `format=json`，`top_n=5`。展开偏好、原话、修正记录、候选总数、推荐及下一步建议。 |
| `GET /sessions/{session_id}/report` | 改为 `format=md`，`top_n=11`。在 Markdown 中定位 P012 的收入待确认项，展示同一报告结构的另一种返回格式。 |

## S-B 展示建议确认

用新会话提交工作簿「模拟咨询对话」的 S-B 第 1–4 轮。核对城市、方向未限定，出差上限为偶尔，候选共 17 个。加班 T013 保存首选少、可接受到偶尔。P009 的偶尔加班得 0.5 分，岗位仍可推荐。

T007、T012 出现在 `pending_suggestions`。选取 T007 的 `evidence_turn_id`，用 `PUT /sessions/{session_id}/preferences/T007` 显式确认。

```json
{"action":"confirm","evidence_turn_id":42}
```

请求中的 `42` 替换为实际返回值。再次查画像和匹配，展示 T007 进入正式软偏好、修正记录引用原发言，候选数仍为 17。重复同一确认请求会返回 404。

## S-C 展示资格参考

用新会话提交工作簿 S-C 的第 1–3 轮。展示初始硬条件为成都、体制内、出差上限无，得到 P001、P005、P012、P018。当前资格为本科、群众、0 年经验。

继续提交第 4–5 轮，展示重庆追加、出差上限放宽到偶尔，候选增至 11 个。查看 P008 的硕士和 2 年经验准备提示，以及 P019 的政治面貌和经验差距。调用报告时用 `top_n=11` 展示这些岗位。

## 批量复现与异常展示

在服务运行期间执行 `uv run --locked python -m scripts.demo`，逐场输出候选数、硬条件、资格、讲解、追问和建议。真实模型的具体措辞可能变化，候选基线以同一画像下的确定性规则为准。

演示失败时先看 HTTP 状态。422 表示请求内容未通过校验。502 表示本轮抽取失败，可查画像确认旧值仍在。岗位讲解、追问和报告生成失败时会使用资料支持的回退内容。完整真实模型演示需要可用的模型配置，固定响应测试的命令见 [README](README.md)。
