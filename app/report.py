import json
import logging
from dataclasses import dataclass
from uuid import UUID

import httpx
from pydantic import BaseModel, Field

from app.advisor import FollowUpQuestion, PROHIBITED_CLAIMS, follow_up_questions
from app.db import connect
from app.dictionary import load_dictionary
from app.llm import JsonModel, OpenAICompatibleClient
from app.matcher import MatchResult, match_session
from app.profile import Preference, PreferenceChange, PreferenceSuggestion, get_profile

logger = logging.getLogger(__name__)
ADVICE_PROMPT = """为未来职业规划选择简短的下一步建议。只输出 JSON {"step_ids":["步骤编号"]}，最多选三项并按优先级排列。
只能从提供的选项中选择。岗位要求用于准备提示，不能宣称已经具备报考资格。不得承诺薪酬、编制、晋升或竞争结果；不得虚构未提供的信息。"""


class AdviceDraft(BaseModel):
    step_ids: list[str] = Field(max_length=3)


@dataclass(frozen=True)
class NamedPreference:
    name: str
    preference: Preference


@dataclass(frozen=True)
class ConfirmJobDetail:
    job_id: str
    tag_id: str
    message: str


@dataclass(frozen=True)
class PlanningReport:
    session_id: UUID
    mode: str
    hard_preferences: list[NamedPreference]
    soft_preferences: list[NamedPreference]
    qualifications: dict
    preference_changes: list[PreferenceChange]
    pending_suggestions: list[PreferenceSuggestion]
    candidate_count: int
    recommended_jobs: list[MatchResult]
    questions: list[FollowUpQuestion]
    job_details_to_confirm: list[ConfirmJobDetail]
    advice: list[str]


def build_report(session_id: UUID, top_n: int, model: JsonModel | None = None) -> PlanningReport:
    profile = get_profile(session_id)
    matches = match_session(session_id)
    with connect() as conn:
        definition = load_dictionary(conn)

    hard = []
    soft = []
    for preference in profile.preferences.values():
        item = NamedPreference(definition[preference.tag_id].name, preference)
        (hard if preference.strength == "hard" else soft).append(item)

    recommended = matches[:top_n]
    questions = follow_up_questions(profile, matches, model)
    unknown = [
        ConfirmJobDetail(job.job_id, item.tag_id, item.detail)
        for job in recommended for item in job.hard_conditions + job.soft_scores
        if item.status == "pending_confirmation"
    ]

    options = {}
    for job in recommended:
        for hint in job.qualification_hints:
            if hint.status == "preparation_needed":
                options[f"qualification:{job.job_id}:{hint.tag_id}"] = f"将 {job.job_id} 的资格差距纳入准备计划。{hint.detail}。"
    for item in unknown:
        options[f"verify:{item.job_id}:{item.tag_id}"] = f"联系咨询师核实 {item.job_id} 的{definition[item.tag_id].name}。"
    for question in questions:
        options[f"clarify:{question.tag_id}"] = f"与学生进一步确认对{question.tag_name}的偏好。"
    if recommended:
        options["compare"] = "对照推荐岗位的匹配依据和未满足项，列出愿意优先了解的岗位。"
    else:
        options["review"] = "与学生复核已明确的硬条件，确认是否愿意扩大职业探索范围。"
    options = {key: value for key, value in options.items() if not PROHIBITED_CLAIMS.search(value)}

    selected = []
    try:
        backend = model if model is not None else OpenAICompatibleClient.from_env()
        draft = AdviceDraft.model_validate_json(backend.complete_json(ADVICE_PROMPT, json.dumps(options, ensure_ascii=False)))
        selected = list(dict.fromkeys(step for step in draft.step_ids if step in options))
    except (httpx.HTTPError, ValueError, KeyError):
        logger.warning("规划模型未返回有效结果，使用资料支持的准备建议")
    qualification_step = next((key for key in options if key.startswith("qualification:")), None)
    if qualification_step and not any(key.startswith("qualification:") for key in selected):
        selected.insert(0, qualification_step)
    selected = list(dict.fromkeys([*selected, *options]))[:3]

    return PlanningReport(
        session_id=session_id, mode="future_planning", hard_preferences=hard, soft_preferences=soft,
        qualifications=profile.qualifications, preference_changes=profile.changes,
        pending_suggestions=list(profile.pending_suggestions.values()), candidate_count=len(matches),
        recommended_jobs=recommended, questions=questions, job_details_to_confirm=unknown,
        advice=[options[key] for key in selected],
    )


def render_markdown(report: PlanningReport) -> str:
    lines = ["# 职业规划报告", "", "本报告用于未来职业意向咨询。", ""]
    for title, items in (("硬条件", report.hard_preferences), ("软偏好", report.soft_preferences)):
        lines += [f"## {title}", ""]
        for item in items:
            raw = item.preference.evidence_text
            evidence = " ".join(raw.splitlines()) if raw else "手动设置"
            value = json.dumps(item.preference.value, ensure_ascii=False)
            lines.append(f"- {item.name}（{item.preference.tag_id}） · {value} · 依据「{evidence}」")
        if not items:
            lines.append("尚未明确。")
        lines.append("")

    lines += ["## 当前资格与修正记录", ""]
    for field, name in (("education", "学历"), ("political_status", "政治面貌"), ("experience_years", "工作经验（年）")):
        if field in report.qualifications:
            lines.append(f"- {name} · {report.qualifications[field]}")
    if not report.qualifications:
        lines.append("当前资格尚未提供。")
    for change in report.preference_changes:
        turn = f"第 {change.turn_no} 轮" if change.turn_no is not None else "手动修改"
        previous = json.dumps(change.old_value, ensure_ascii=False)
        latest = json.dumps(change.new_value, ensure_ascii=False)
        lines.append(f"- {turn} · {change.tag_id} · {previous} → {latest} · 「{' '.join(change.reason.splitlines())}」")

    lines += ["", "## 推荐岗位", "", f"共 {report.candidate_count} 个候选，列出其中 {len(report.recommended_jobs)} 个。", ""]
    for job in report.recommended_jobs:
        score = f"{job.score:.2f}" if job.score is not None else "暂无评分"
        status = "存在待确认硬条件" if job.hard_condition_status == "pending_confirmation" else (
            "已知硬条件均满足" if job.hard_conditions else "暂未设置硬条件"
        )
        lines += [f"### {job.job_id} {job.name or '岗位名称未提供'}", "",
                  f"得分 {score} · {status} · 评分覆盖 {job.scoring_coverage.scored_preferences} 项，"
                  f"未知 {job.scoring_coverage.unknown_preferences} 项。", ""]
        for reason in job.match_reasons:
            evidence = f"（依据：{reason.evidence}）" if reason.evidence else ""
            lines.append(f"- {reason.detail}{evidence}")
        lines.extend(f"- 未满足 · {item.detail}" for item in job.unmet_preferences)
        lines.extend(f"- 待确认 · {item.message}" for item in report.job_details_to_confirm if item.job_id == job.job_id)
        lines.extend(f"- 资格参考 · {hint.detail}" for hint in job.qualification_hints)
        lines.append("")

    lines += ["## 待确认问题", ""]
    lines.extend(f"- {item.text}" for item in report.questions)
    lines.extend(f"- {item.tag_id} 待确认建议 · 避开「{item.value['avoid']}」 · 原话「{item.evidence_text}」"
                 for item in report.pending_suggestions)
    if not report.questions and not report.pending_suggestions and not report.job_details_to_confirm:
        lines.append("暂无待确认问题。")
    lines += ["", "## 职业规划建议", ""]
    lines.extend(f"- {step}" for step in report.advice)
    return "\n".join(lines) + "\n"
