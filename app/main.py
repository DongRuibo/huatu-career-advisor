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


def get_model() -> JsonModel | None:
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


@app.exception_handler(JobNotFoundError)
@app.exception_handler(SessionNotFoundError)
@app.exception_handler(SuggestionNotFoundError)
def not_found(_request: Request, error: LookupError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(error)})


@app.post("/sessions", status_code=201, summary="创建咨询会话")
def post_session() -> dict:
    return {"session_id": create_session(), "mode": "future_planning"}


@app.post("/sessions/{session_id}/turns", status_code=201, summary="提交一轮对话")
def post_turn(session_id: UUID, turn: TurnInput, model: ModelDependency) -> TurnResult:
    try:
        return submit_turn(session_id, turn, model)
    except KeyError as error:
        raise HTTPException(503, "模型服务配置不完整") from error
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(502, "标签抽取服务未返回可用结果") from error


@app.get("/sessions/{session_id}/preferences", summary="查看当前画像和修正记录")
def preferences(session_id: UUID) -> Profile:
    return get_profile(session_id)


@app.put("/sessions/{session_id}/preferences/{tag_id}", summary="手动改偏好或确认待确认建议")
def put_preference(
    session_id: UUID, tag_id: str,
    payload: Annotated[SetPreferenceInput | ConfirmSuggestionInput, Body(discriminator="action")],
) -> Profile:
    if isinstance(payload, ConfirmSuggestionInput):
        confirm_suggestion(session_id, tag_id, payload.evidence_turn_id)
    else:
        try:
            set_manual_preference(session_id, tag_id, payload.value, payload.strength, payload.constraint_type)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    return get_profile(session_id)


@app.get("/sessions/{session_id}/matches", summary="查询岗位匹配结果")
def matches(session_id: UUID) -> list[MatchResult]:
    return match_session(session_id)


@app.get("/sessions/{session_id}/jobs/{job_id}/explain", summary="生成有来源的岗位讲解")
def job_explanation(session_id: UUID, job_id: str, model: ModelDependency) -> JobExplanation:
    with connect() as conn:
        require_session(conn, session_id)
        details = read_job(conn, job_id)
    return explain_job(details, model)


@app.get("/sessions/{session_id}/questions", summary="根据候选岗位区分度生成咨询问题")
def questions(session_id: UUID, model: ModelDependency) -> list[FollowUpQuestion]:
    return follow_up_questions(get_profile(session_id), match_session(session_id), model)


@app.get(
    "/sessions/{session_id}/report", summary="生成 JSON 或 Markdown 规划报告", response_model=PlanningReport,
    responses={200: {"content": {"text/markdown": {"schema": {"type": "string"}}}}},
)
def planning_report(
    session_id: UUID, model: ModelDependency,
    format: Literal["md", "json"] = "json", top_n: Annotated[int, Query(gt=0)] = 5,
) -> PlanningReport | Response:
    report = build_report(session_id, top_n, model)
    if format == "md":
        return Response(content=render_markdown(report), media_type="text/markdown")
    return report


@app.get("/jobs", summary="按城市、方向和标签查询岗位")
def job_list(city: str | None = None, direction: str | None = None, tag: str | None = None) -> list[dict]:
    tag_filter = None
    if tag:
        tag_id, separator, value = tag.partition(":")
        if not separator or not tag_id.strip() or not value.strip():
            raise HTTPException(422, "tag 使用 标签ID:标签值 格式")
        tag_filter = (tag_id.strip(), value.strip())
    return list_jobs(city or None, direction or None, tag_filter)


@app.get("/jobs/{job_id}", summary="查看岗位、标签及知识库资料")
def job_details(job_id: str) -> JobDetails:
    return get_job(job_id)


@app.post("/jobs", status_code=201, summary="新增岗位")
def post_job(payload: JobCreate) -> JobDetails:
    try:
        return save_job(payload.job_id, payload, create=True)
    except JobConflictError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.put("/jobs/{job_id}", summary="修改岗位字段或替换业务标签")
def put_job(job_id: str, payload: JobUpdate) -> JobDetails:
    try:
        return save_job(job_id, payload)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/import/issues", summary="查看最近一次或指定批次的导入报告")
def import_issues(run_id: UUID | None = None) -> dict:
    with connect() as conn:
        report = get_import_report(conn, run_id)
    if report is None:
        raise HTTPException(404, "导入批次不存在")
    return report
