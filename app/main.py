# FastAPI 服务入口，定义会话、对话、画像、匹配、咨询报告和岗位维护接口。
# 这里负责请求参数、模型依赖注入、响应格式和 HTTP 状态，具体业务规则由对应模块执行。
# 咨询流程从创建会话开始，后续接口用 session_id 读取或更新同一份画像。

from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.advisor import FollowUpQuestion, JobExplanation, explain_job, follow_up_questions
from app.db import connect
from app.etl import get_import_report
from app.extractor import ConstraintType, OvertimePreference, Strength, TurnInput
from app.jobs import JobConflictError, JobCreate, JobDetails, JobNotFoundError, JobUpdate, get_job, list_jobs, read_job, save_job
from app.llm import JsonModel
from app.matcher import MatchResult, match_session
from app.profile import (
    Profile, SessionNotFoundError, SuggestionNotFoundError, TurnResult, confirm_suggestion, create_session, get_profile,
    require_session, set_manual_preference, submit_turn,
)
from app.report import PlanningReport, build_report, render_markdown

app = FastAPI(title="AI 职业规划辅助系统", version="0.1.0")


# 提供可替换的模型依赖。默认值让各业务模块按需创建客户端，测试可由依赖注入提供替身。
def get_model() -> JsonModel | None:
    # 正常启动时由业务层按环境变量创建客户端，测试可覆盖这项依赖。
    return None


ModelDependency = Annotated[JsonModel | None, Depends(get_model)]


class SetPreferenceInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    action: Literal["set"]
    value: str | list[str] | OvertimePreference
    strength: Strength
    constraint_type: ConstraintType


class ConfirmSuggestionInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    action: Literal["confirm"]
    evidence_turn_id: int = Field(gt=0)


# 将会话、岗位或建议的未找到错误统一转换为含 detail 的 HTTP 404 响应。
@app.exception_handler(JobNotFoundError)
@app.exception_handler(SessionNotFoundError)
@app.exception_handler(SuggestionNotFoundError)
def not_found(_request: Request, error: LookupError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(error)})


# 创建未来规划会话并返回新的 UUID，后续对话和匹配通过该 UUID 关联。
@app.post("/sessions", status_code=201, summary="创建咨询会话")
def post_session() -> dict:
    return {"session_id": create_session(), "mode": "future_planning"}


# 接收会话与一轮发言，返回提交后的变更明细，并区分模型配置与抽取异常。
@app.post("/sessions/{session_id}/turns", status_code=201, summary="提交一轮对话")
def post_turn(session_id: UUID, turn: TurnInput, model: ModelDependency) -> TurnResult:
    # 将模型配置缺失和抽取失败映射为不同状态，便于前端显示对应提示。
    try:
        return submit_turn(session_id, turn, model)
    except KeyError as error:
        raise HTTPException(503, "模型服务配置不完整") from error
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(502, "标签抽取服务未返回可用结果") from error


# 返回指定会话当前的正式偏好、资格、变更历史和待确认建议。
@app.get("/sessions/{session_id}/preferences", summary="查看当前画像和修正记录")
def preferences(session_id: UUID) -> Profile:
    return get_profile(session_id)


# 将写请求分流到手动设定或确认建议，成功后返回服务端保存的完整画像。
@app.put("/sessions/{session_id}/preferences/{tag_id}", summary="手动改偏好或确认待确认建议")
def put_preference(
    session_id: UUID, tag_id: str,
    payload: Annotated[SetPreferenceInput | ConfirmSuggestionInput, Body(discriminator="action")],
) -> Profile:
    # action 决定请求体类型。两种写操作结束后都读取完整画像供工作台刷新。
    if isinstance(payload, ConfirmSuggestionInput):
        confirm_suggestion(session_id, tag_id, payload.evidence_turn_id)
    else:
        try:
            set_manual_preference(session_id, tag_id, payload.value, payload.strength, payload.constraint_type)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    return get_profile(session_id)


# 根据已生效画像计算指定会话的候选岗位、得分和匹配依据。
@app.get("/sessions/{session_id}/matches", summary="查询岗位匹配结果")
def matches(session_id: UUID) -> list[MatchResult]:
    return match_session(session_id)


# 核对会话并读取指定岗位的资料，交给讲解模块生成带来源的说明。
@app.get("/sessions/{session_id}/jobs/{job_id}/explain", summary="生成有来源的岗位讲解")
def job_explanation(session_id: UUID, job_id: str, model: ModelDependency) -> JobExplanation:
    with connect() as conn:
        require_session(conn, session_id)
        details = read_job(conn, job_id)
    # 先把指定岗位资料读全并释放连接，再调用可能较慢的模型服务。
    return explain_job(details, model)


# 基于当前画像和一轮规则匹配结果生成咨询师可继续问的问题。
@app.get("/sessions/{session_id}/questions", summary="根据候选岗位区分度生成咨询问题")
def questions(session_id: UUID) -> list[FollowUpQuestion]:
    return follow_up_questions(get_profile(session_id), match_session(session_id))


# 根据格式和推荐数量生成报告，把同一结构化内容返回为 JSON 或 Markdown。
@app.get(
    "/sessions/{session_id}/report", summary="生成 JSON 或 Markdown 规划报告", response_model=PlanningReport,
    responses={200: {"content": {"text/markdown": {"schema": {"type": "string"}}}}},
)
def planning_report(
    session_id: UUID, model: ModelDependency,
    format: Literal["md", "json"] = "json", top_n: Annotated[int, Query(gt=0)] = 5,
) -> PlanningReport | Response:
    # 先构造统一的报告数据，再根据请求选择 JSON 或 Markdown。
    report = build_report(session_id, top_n, model)
    if format == "md":
        return Response(content=render_markdown(report), media_type="text/markdown")
    return report


# 解析岗位查询中的标签参数，再按城市、方向和标签取值筛选。
@app.get("/jobs", summary="按城市、方向和标签查询岗位")
def job_list(city: str | None = None, direction: str | None = None, tag: str | None = None) -> list[dict]:
    tag_filter = None
    if tag:
        tag_id, separator, value = tag.partition(":")
        if not separator or not tag_id.strip() or not value.strip():
            raise HTTPException(422, "tag 使用 标签ID:标签值 格式")
        tag_filter = (tag_id.strip(), value.strip())
    return list_jobs(city or None, direction or None, tag_filter)


# 按岗位 ID 返回字段、标签和知识资料，供直接查询和校验维护结果。
@app.get("/jobs/{job_id}", summary="查看岗位、标签及知识库资料")
def job_details(job_id: str) -> JobDetails:
    return get_job(job_id)


# 新增岗位及其标签，对重复岗位 ID 和字典校验失败返回相应的 HTTP 状态。
@app.post("/jobs", status_code=201, summary="新增岗位")
def post_job(payload: JobCreate) -> JobDetails:
    try:
        return save_job(payload.job_id, payload, create=True)
    except JobConflictError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


# 按路径中的岗位 ID 修改已传入字段，传入 tags 时替换业务标签。
@app.put("/jobs/{job_id}", summary="修改岗位字段或替换业务标签")
def put_job(job_id: str, payload: JobUpdate) -> JobDetails:
    try:
        return save_job(job_id, payload)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


# 返回指定或最近一次 Excel 导入批次的报告。没有对应批次时返回 404。
@app.get("/import/issues", summary="查看最近一次或指定批次的导入报告")
def import_issues(run_id: UUID | None = None) -> dict:
    with connect() as conn:
        report = get_import_report(conn, run_id)
    if report is None:
        raise HTTPException(404, "导入批次不存在")
    return report
