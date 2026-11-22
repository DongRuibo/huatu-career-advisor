# 将当前画像、候选岗位、待确认问题和下一步建议组合成职业规划报告。
# 推荐清单沿用匹配引擎的排序。模型从程序提供的建议中选择，事实来源由代码控制。
# 结构化报告可由接口直接序列化为 JSON，也可渲染为 Markdown。

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


# 汇总会话画像、匹配清单、追问和可准备事项，返回两种报告格式共用的数据。
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

    # 推荐清单按请求数量截取，智能追问仍基于完整候选集计算区分度。
    recommended = matches[:top_n]
    questions = follow_up_questions(profile, matches)
    # 把入选岗位的未知项单列为核实事项，便于报告读者找到待确认资料。
    unknown = [
        ConfirmJobDetail(job.job_id, item.tag_id, item.detail)
        for job in recommended for item in job.hard_conditions + job.soft_scores
        if item.status == "pending_confirmation"
    ]

    # 用当前候选和待确认事项生成具体行动，模型只选择和排序已有文案。
    options = {}
    priority_jobs = [
        job.job_id for job in recommended
        if job.hard_condition_status == "confirmed"
        and all(hint.status != "preparation_needed" for hint in job.qualification_hints)
    ][:3]
    if recommended:
        focus = f"先对比 {'、'.join(priority_jobs)}" if priority_jobs else "从推荐清单中选出最感兴趣的岗位"
        options["compare"] = f"{focus}，了解工作内容、工作方式和成长方向，再按学生最看重的方面缩小范围。"
        if unknown or any(hint.status == "pending_confirmation" for job in recommended for hint in job.qualification_hints):
            options["compare"] += "把清单中的待确认信息列出来，逐项核实。"
    else:
        options["review"] = "和学生逐项确认哪些条件必须满足。如果有可以放宽的条件，调整后再比较候选岗位。"

    # 学历和工作经验差距可转为准备事项，其他资格仍在岗位参考中展示。
    qualification_jobs: dict[str, list[str]] = {}
    for job in recommended:
        for hint in job.qualification_hints:
            if hint.tag_id in {"T017", "T018"} and hint.status == "preparation_needed":
                qualification_jobs.setdefault(hint.tag_id, []).append(job.job_id)
    preparation = []
    for tag_id, job_ids in qualification_jobs.items():
        group = "、".join(job_ids[:3])
        if len(job_ids) > 3:
            group += f" 等 {len(job_ids)} 个岗位"
        if tag_id == "T018":
            preparation.append(f"关注 {group} 时，先核实哪些经历可计入工作经验，再据此安排实习或项目实践，并保存经历证明。")
        else:
            name = definition[tag_id].name
            preparation.append(f"核实 {group} 的{name}，再据此更新长期准备清单。")
    if preparation:
        options["qualification"] = "".join(preparation)
    if questions or profile.pending_suggestions:
        options["clarify"] = "请学生回答上面的偏好问题，选出最看重的一项，再用这个偏好区分候选岗位。"
    options = {key: value for key, value in options.items() if not PROHIBITED_CLAIMS.search(value)}

    selected = []
    try:
        backend = model if model is not None else OpenAICompatibleClient.from_env()
        draft = AdviceDraft.model_validate_json(backend.complete_json(ADVICE_PROMPT, json.dumps(options, ensure_ascii=False)))
        selected = list(dict.fromkeys(step for step in draft.step_ids if step in options))
    except (httpx.HTTPError, ValueError, KeyError):
        logger.warning("规划模型未返回有效结果，使用资料支持的准备建议")
    # 最多三类行动，保留模型顺序并补全其余类别。模型失败时沿用规则顺序。
    selected = list(dict.fromkeys([*selected, *options]))

    return PlanningReport(
        session_id=session_id, mode="future_planning", hard_preferences=hard, soft_preferences=soft,
        qualifications=profile.qualifications, preference_changes=profile.changes,
        pending_suggestions=list(profile.pending_suggestions.values()), candidate_count=len(matches),
        recommended_jobs=recommended, questions=questions, job_details_to_confirm=unknown,
        advice=[options[key] for key in selected],
    )


# 用普通读者能直接使用的语言呈现当前画像和岗位依据。审计字段由 JSON 返回。
def render_markdown(report: PlanningReport) -> str:
    lines = ["# 职业规划报告", "", "本报告用于职业规划参考，实际岗位要求以招聘信息为准。", "",
             "## 职业意向概览", ""]
    for title, items in (("必须满足的条件", report.hard_preferences), ("更看重的特点", report.soft_preferences)):
        preferences = []
        for item in items:
            value = item.preference.value
            if isinstance(value, list):
                description = "、".join(value)
            elif isinstance(value, dict):
                description = (f"希望避开{value['avoid']}" if "avoid" in value
                               else f"偏好为{value['preferred']}，最多接受{value['acceptable_max']}")
            else:
                prefix = {"max": "最多接受", "min": "至少"}.get(item.preference.constraint_type, "")
                description = f"{prefix if value != '无' else ''}{value}"
            preferences.append(f"{item.name}（{description}）")
        lines.append(f"- **{title}** · {('，'.join(preferences) or '尚未明确')}。")

    qualifications = [
        f"{name}{report.qualifications[field]}{suffix}"
        for field, name, suffix in (("education", "学历", ""), ("political_status", "政治面貌", ""),
                                    ("experience_years", "工作经验", "年"))
        if field in report.qualifications
    ]
    lines.append(f"- **当前情况** · {('，'.join(qualifications) or '尚未提供学历、政治面貌及工作经验')}。")

    lines += ["", "## 岗位匹配概况", ""]
    if report.recommended_jobs:
        lines.append(f"共找到 {report.candidate_count} 个候选岗位，以下展示 {len(report.recommended_jobs)} 个。")
    else:
        lines.append("当前条件下暂无候选岗位。")
    scored_counts = {job.scoring_coverage.scored_preferences for job in report.recommended_jobs if job.score is not None}
    if scored_counts:
        scope = ("每个已评分岗位只有 1 项已知软偏好参与评分" if scored_counts == {1}
                 else "岗位只按有资料的软偏好评分")
        lines += ["", f"{scope}。1.00 表示参与评分的偏好均得到满足。"
                  "评估岗位整体适合度还需结合更多信息，录用以实际招聘结果为准。"]
    elif report.recommended_jobs:
        lines += ["", "当前暂无可用于评分的软偏好资料。"]

    if report.recommended_jobs:
        lines += ["", "## 推荐岗位", "", "| 岗位 | 主要匹配依据 | 需留意 |", "| --- | --- | --- |"]
    for job in report.recommended_jobs:
        reasons = [
            f"{item.tag_name}为{item.job_value}" + ("（部分符合偏好）" if item.status == "partially_matched" else "")
            for item in job.match_reasons
        ]
        cautions = [item.message for item in report.job_details_to_confirm if item.job_id == job.job_id]
        cautions.extend(item.detail for item in job.unmet_preferences)
        hints = [hint.detail for hint in job.qualification_hints if hint.status != "meets_requirement"]
        if not hints:
            hints = [hint.detail for hint in job.qualification_hints]
        if hints:
            cautions.append(f"资格参考：{'。'.join(hints)}")
        cells = (f"{job.job_id} {job.name or '岗位名称未提供'}", "，".join(reasons) or "暂无已确认的匹配依据",
                 "。".join(cautions) or "—")
        lines.append("| " + " | ".join(" ".join(cell.splitlines()).replace("|", r"\|") for cell in cells) + " |")

    lines += ["", "## 待确认问题", ""]
    questions = [item.text for item in report.questions]
    asked_tags = {item.tag_id for item in report.questions}
    questions.extend(f"你是否希望避开「{item.value['avoid']}」这类岗位？"
                     for item in report.pending_suggestions if item.tag_id not in asked_tags)
    if questions:
        lines.extend(f"- {question}" for question in questions)
    else:
        lines.append("暂无需要补充的职业偏好问题。")
    lines += ["", "## 职业规划建议", ""]
    lines.extend(f"{number}. {step}" for number, step in enumerate(report.advice, start=1))
    return "\n".join(lines) + "\n"
