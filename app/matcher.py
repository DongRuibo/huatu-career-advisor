# 确定性岗位匹配引擎，使用正式画像、岗位数据和标签字典生成候选与评分明细。
# 先核对硬条件，再按已知软偏好评分，同时保留未知项和评分覆盖情况。
# 资格条件单独生成参考提示，排序和理由由同一次规则计算得出。

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from statistics import mean
from typing import Literal, cast
from uuid import UUID

from psycopg.rows import dict_row

from app.db import connect
from app.dictionary import TagDefinition, load_dictionary
from app.extractor import HARD_CONSTRAINTS
from app.profile import ActivePreferenceValue, Preference, Profile, get_profile

type HardStatus = Literal["confirmed", "pending_confirmation"]
type AssessmentStatus = Literal["confirmed", "matched", "partially_matched", "unmet", "pending_confirmation"]
type QualificationStatus = Literal["meets_requirement", "preparation_needed", "pending_confirmation", "reference_only"]


@dataclass(frozen=True)
class JobTag:
    value: str
    evidence: str | None = None


@dataclass(frozen=True)
class MatchJob:
    job_id: str
    name: str | None
    is_matchable: bool
    city: str | None = None
    direction: str | None = None
    education_req: str | None = None
    experience_years: int | None = None
    political_req: str | None = None
    major_req: str | None = None
    tags: dict[str, JobTag] = field(default_factory=dict)


@dataclass(frozen=True)
class PreferenceAssessment:
    tag_id: str
    tag_name: str
    preference_value: ActivePreferenceValue
    job_value: str | None
    evidence: str | None
    status: AssessmentStatus
    score: float | None
    detail: str


@dataclass(frozen=True)
class ScoringCoverage:
    scored_preferences: int
    unknown_preferences: int


@dataclass(frozen=True)
class QualificationHint:
    tag_id: str
    requirement: str | int | None
    current: str | int | None
    status: QualificationStatus
    detail: str


@dataclass(frozen=True)
class MatchResult:
    job_id: str
    name: str | None
    city: str | None
    direction: str | None
    score: float | None
    hard_condition_status: HardStatus
    hard_conditions: list[PreferenceAssessment]
    scoring_coverage: ScoringCoverage
    soft_scores: list[PreferenceAssessment]
    match_reasons: list[PreferenceAssessment]
    unmet_preferences: list[PreferenceAssessment]
    unknown_tags: list[str]
    qualification_hints: list[QualificationHint]


# 读取某个岗位的标签事实，城市和方向以主表为准。没有有效取值时返回空值。
def job_fact(job: MatchJob, definition: TagDefinition) -> JobTag | None:
    # 城市和方向以岗位字段为准。其余取值须通过字典校验才用于筛选或评分。
    tag = job.tags.get(definition.tag_id)
    if definition.tag_id == "T001" and (tag is None or tag.value != job.city):
        tag = JobTag(job.city, f"岗位资料的工作城市：{job.city}") if job.city is not None else None
    if definition.tag_id == "T002" and (tag is None or tag.value != job.direction):
        tag = JobTag(job.direction, f"岗位资料的所属方向：{job.direction}") if job.direction is not None else None
    return tag if tag is not None and tag.value in definition.allowed_values else None


# 对已知岗位值计算一项软偏好的 0 到 1 分，支持多选、避开偏好和有序量级。
def soft_score(preference: Preference, job_value: str, definition: TagDefinition) -> float:
    value = preference.value
    # 多选和避开偏好按命中打分，有序标签按字典量级差距降分。
    if isinstance(value, list):
        return float(job_value in value)
    if isinstance(value, dict) and "avoid" in value:
        return float(job_value != value["avoid"])
    if not definition.ordered:
        return float(job_value == value)

    order = definition.allowed_values
    position = order.index(job_value)
    if isinstance(value, dict):
        # 加班超出可接受上限时给零分，仍由软偏好决定排序。
        if position > order.index(value["acceptable_max"]):
            return 0.0
        distance = abs(position - order.index(value["preferred"]))
    else:
        difference = position - order.index(value)
        # 这两个量级在字典中从高到低排列，上下限按实际量级比较。
        if preference.tag_id in {"T004", "T005"}:
            difference = -difference
        if preference.constraint_type == "max":
            distance = max(0, difference)
        elif preference.constraint_type == "min" or preference.tag_id == "T008":
            distance = max(0, -difference)
        else:
            distance = abs(difference)
    # 相差一个量级扣半分，最低为零，避免用负分改变已知项平均值的含义。
    return max(0.0, 1.0 - distance * 0.5)


# 对照岗位参考要求和用户已知资格，生成资格提示，供展示和报告引用。
def qualification_hints(profile: Profile, job: MatchJob) -> list[QualificationHint]:
    education_order = ["本科", "硕士"]
    fields = (
        ("T017", "学历", job.education_req, profile.qualifications.get("education")),
        ("T018", "工作经验", job.experience_years, profile.qualifications.get("experience_years")),
        ("T019", "政治面貌", job.political_req, profile.qualifications.get("political_status")),
        ("T020", "专业", job.major_req, None),
    )
    hints = []
    for tag_id, name, requirement, current in fields:
        # 不限要求无需提示。岗位要求未知或咨询方尚未提供时，用待确认提示保留信息缺口。
        if requirement in ("不限", 0):
            continue
        if requirement is None:
            hints.append(QualificationHint(tag_id, requirement, current, "pending_confirmation", f"岗位参考{name}要求未提供，需确认"))
            continue
        if tag_id == "T020":
            hints.append(QualificationHint(tag_id, requirement, None, "reference_only", f"岗位专业参考要求为{requirement}"))
            continue

        suffix = "年" if tag_id == "T018" else ""
        requirement_text = f"岗位参考{name}要求为{requirement}{suffix}"
        if current is None:
            hints.append(QualificationHint(
                tag_id, requirement, current, "pending_confirmation", f"{requirement_text}，当前{name}未提供，需确认",
            ))
            continue
        if tag_id == "T017":
            if current not in education_order or requirement not in education_order:
                hints.append(QualificationHint(
                    tag_id, requirement, current, "pending_confirmation", f"{requirement_text}，当前为{current}，需确认学历对应关系",
                ))
                continue
            meets = education_order.index(current) >= education_order.index(requirement)
        elif tag_id == "T018":
            meets = cast(int, current) >= cast(int, requirement)
        else:
            if current != requirement:
                hints.append(QualificationHint(
                    tag_id, requirement, current, "reference_only",
                    f"{requirement_text}，当前为{current}，存在资格条件差异，需以实际招聘公告为准",
                ))
                continue
            meets = True

        status = "meets_requirement" if meets else "preparation_needed"
        description = "符合参考要求" if meets else "可作为长期准备方向"
        hints.append(QualificationHint(tag_id, requirement, current, status, f"{requirement_text}，当前为{current}{suffix}，{description}"))
    return hints


# 用已加载的数据筛选硬条件并计算软偏好均分，返回稳定排序的候选及解释明细。
def match_jobs(
    profile: Profile, jobs: Iterable[MatchJob], dictionary: Mapping[str, TagDefinition],
) -> list[MatchResult]:
    # 资格字段留给后面的参考提示，避免把当前报考资格当作未来规划的硬条件。
    preferences = [
        (preference, dictionary[tag_id]) for tag_id, preference in sorted(profile.preferences.items())
        if dictionary[tag_id].match_rule != "资格参考"
    ]
    hard = [(p, d) for p, d in preferences if p.strength == "hard" and p.tag_id in HARD_CONSTRAINTS]
    soft = [(p, d) for p, d in preferences if p.strength != "hard" or p.tag_id not in HARD_CONSTRAINTS]

    matches = []
    for job in jobs:
        if not job.is_matchable:
            continue
        hard_conditions = []
        rejected = False
        for preference, definition in hard:
            fact = job_fact(job, definition)
            if fact is None:
                # 缺资料的硬条件先标记待确认，只有已知不满足时才淘汰岗位。
                name = "出差情况" if preference.tag_id == "T003" else definition.name
                hard_conditions.append(PreferenceAssessment(
                    preference.tag_id, definition.name, preference.value, None, None,
                    "pending_confirmation", None, f"{name}未知，需确认",
                ))
                continue
            if preference.tag_id == "T003":
                # 出差频率按允许值中的顺序比较上限，城市和职业方向按允许范围匹配。
                order = definition.allowed_values
                confirmed = order.index(fact.value) <= order.index(cast(str, preference.value))
            else:
                confirmed = fact.value in cast(list[str], preference.value)
            if not confirmed:
                rejected = True
                break
            hard_conditions.append(PreferenceAssessment(
                preference.tag_id, definition.name, preference.value, fact.value, fact.evidence,
                "confirmed", None, f"岗位{definition.name}为{fact.value}，满足硬条件",
            ))
        if rejected:
            continue

        soft_scores = []
        confirmed_scores = []
        for preference, definition in soft:
            fact = job_fact(job, definition)
            if fact is None:
                # 未知项只进入明细和覆盖统计，已知的不满足则以零分计入平均。
                soft_scores.append(PreferenceAssessment(
                    preference.tag_id, definition.name, preference.value, None, None,
                    "pending_confirmation", None, f"岗位{definition.name}未知，需确认",
                ))
                continue
            score = soft_score(preference, fact.value, definition)
            confirmed_scores.append(score)
            if score == 1.0:
                status, description = "matched", "满足软偏好"
            elif score > 0:
                status, description = "partially_matched", "部分满足软偏好"
            else:
                status, description = "unmet", "未满足软偏好"
            soft_scores.append(PreferenceAssessment(
                preference.tag_id, definition.name, preference.value, fact.value, fact.evidence,
                status, score, f"岗位{definition.name}为{fact.value}，{description}",
            ))

        # 分开汇总未知硬条件与未知软偏好，候选状态和评分覆盖分别使用各自的统计。
        unknown_hard = [item.tag_id for item in hard_conditions if item.status == "pending_confirmation"]
        unknown_soft = [item.tag_id for item in soft_scores if item.status == "pending_confirmation"]
        matches.append(MatchResult(
            job_id=job.job_id, name=job.name, city=job.city, direction=job.direction,
            score=mean(confirmed_scores) if confirmed_scores else None,
            hard_condition_status="pending_confirmation" if unknown_hard else "confirmed",
            hard_conditions=hard_conditions,
            scoring_coverage=ScoringCoverage(len(confirmed_scores), len(unknown_soft)),
            soft_scores=soft_scores,
            match_reasons=[
                item for item in hard_conditions + soft_scores
                if item.status in {"confirmed", "matched", "partially_matched"}
            ],
            unmet_preferences=[item for item in soft_scores if item.status == "unmet"],
            unknown_tags=unknown_hard + unknown_soft,
            qualification_hints=qualification_hints(profile, job),
        ))
    # 硬条件已确认的岗位优先。同组按分数降序，零分也排在无分数之前，同分按岗位 ID 排序。
    return sorted(matches, key=lambda match: (
        match.hard_condition_status == "pending_confirmation",
        match.score is None,
        -(match.score or 0.0),
        match.job_id,
    ))


# 组装会话画像、可匹配岗位和标签字典，将数据交给不访问数据库的匹配函数。
def match_session(session_id: UUID) -> list[MatchResult]:
    # 在这里读取数据库并组装岗位。match_jobs 接收已加载的数据，可直接用固定样本测试。
    profile = get_profile(session_id)
    with connect() as conn:
        dictionary = load_dictionary(conn)
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT job_id, name, is_matchable, city, direction, education_req, experience_years, political_req, major_req "
                "FROM jobs WHERE is_matchable"
            )
            jobs = {row["job_id"]: MatchJob(**row) for row in cursor}
            cursor.execute(
                "SELECT job_id, tag_id, value, evidence FROM job_tags WHERE job_id = ANY(%s)",
                (list(jobs),),
            )
            for row in cursor:
                jobs[row["job_id"]].tags[row["tag_id"]] = JobTag(row["value"], row["evidence"])
    return match_jobs(profile, jobs.values(), dictionary)
