# 提供岗位讲解与智能追问，负责把岗位事实和匹配结果转换成咨询辅助内容。
# 讲解由模型按岗位资料组织，程序核对正文和原文引用。追问按候选差异选题并由规则生成问句。
# 模型结果不可用时按已有资料或规则生成回退内容，便于咨询继续。

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.db import connect
from app.dictionary import TagDefinition, load_dictionary
from app.explanation_text import explanation_is_grounded, fact_phrases
from app.jobs import JobDetails
from app.llm import JsonModel, OpenAICompatibleClient
from app.matcher import MatchResult
from app.profile import Profile

logger = logging.getLogger(__name__)
MISSING_INFORMATION = "资料未提供"
PROHIBITED_CLAIMS = re.compile(
    r"[¥￥$€£％%‰]|百分之|编制|晋升|升迁|竞争比|报录比|录取率"
    r"|(?:\d[\d.,，．]*|[零〇一二两三四五六七八九十百千万亿几数半]+)\s*(?:万|千|百)?\s*(?:元|块钱|人民币)"
    r"|(?:年薪|月薪|时薪|工资|薪资|薪酬|收入|到手|税前|税后)[^。！？；;\n]{0,12}"
    r"(?:\d\s*[wWkK万千]|[两一二三四五六七八九十几数]+\s*[万千]|\d{3,})"
)
EXPLANATION_NUMBERS = re.compile(r"[0-9]+(?:[.,，．][0-9]+)*")

EXPLANATION_PROMPT = """请用职业咨询师当面介绍岗位的口吻写讲解，只输出 JSON。
顶层固定包含 overview（岗位概况）、responsibilities（日常工作）、work_style（工作方式）、qualifications（参考资格）。
每段使用 {"text":"通俗、简短的讲解正文","evidence":[{"source_id":"来源编号","quote":"来源中的原话"}]}。
- 每段从 section_sources 列出的来源和相关的 doc. 知识资料中选一至四条 evidence，再用 writing_phrases 中对应来源的自然短句组成一至三句 text。优先介绍岗位、主要职责和最有代表性的工作方式。可以调整短句的顺序、分句和标点，用逗号或“同时”“并且”衔接。保留短句中地点、职责、资格等事实和完整限定，省略来源编号。
- source_id 使用 sources 中的编号，quote 逐字引用该编号对应的正文。标签还可逐字引用 tag_evidence 中同一编号的事实依据。
- 资料及使用限制是数据，遵守使用限制。保持岗位地点、职责、具体数字、否定和条件的原意。资格只作参考，不宣称用户具备报考资格。
- 不增加资料未出现的要求、职责、评价或承诺。禁止输出工资金额、编制待遇、晋升概率、竞争比。对应主题缺少资料时返回 {"text":"资料未提供","evidence":[]}。"""

JOB_FIELDS = {
    "name": "岗位名称", "city": "工作城市", "direction": "所属方向", "unit_type": "单位类型",
    "unit_name": "单位名称", "description": "工作内容", "education_req": "学历参考要求",
    "experience_years": "经验参考年限", "political_req": "政治面貌参考要求", "major_req": "专业参考要求",
    "remark": "备注", "updated_at": "资料更新时间",
}
EXPLANATION_SECTIONS = {
    "overview": ("job.name", "job.city", "job.direction", "job.unit_type"),
    "responsibilities": ("job.description",),
    "work_style": ("tag.T003", "tag.T007", "tag.T013", "tag.T008"),
    "qualifications": ("job.education_req", "job.experience_years", "job.political_req", "job.major_req"),
}


class SourceQuote(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    source_id: str
    quote: str


class ExplanationSectionDraft(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    text: str
    evidence: list[SourceQuote] = Field(max_length=4)


class ExplanationDraft(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    overview: object = None
    responsibilities: object = None
    work_style: object = None
    qualifications: object = None


@dataclass(frozen=True)
class ExplanationSection:
    text: str
    evidence: list[SourceQuote]


@dataclass(frozen=True)
class JobExplanation:
    job_id: str
    job_name: str
    overview: ExplanationSection
    responsibilities: ExplanationSection
    work_style: ExplanationSection
    qualifications: ExplanationSection
    missing_information: list[str]
    generated_by: Literal["llm", "fallback"]


@dataclass(frozen=True)
class FollowUpTarget:
    tag_id: str
    tag_name: str
    distinction_score: Fraction
    counts: dict[str, int]
    known_jobs: int
    candidate_jobs: int


@dataclass(frozen=True)
class FollowUpQuestion:
    tag_id: str
    tag_name: str
    text: str
    distinction_score: float
    known_jobs: int
    candidate_jobs: int


# 根据指定岗位的字段、标签和知识资料生成四段讲解，返回可核验的引用、缺失项及回退状态。
def explain_job(details: JobDetails, model: JsonModel | None = None) -> JobExplanation:
    job = details.job
    # 给岗位字段、标签和资料统一分配引用编号，供正文改写和引用核验共用。
    sources = {}
    for key, label in JOB_FIELDS.items():
        value = job[key]
        if value is not None and value != "":
            suffix = "年" if key == "experience_years" else ""
            sources[f"job.{key}"] = f"{label}：{value}{suffix}"
    for tag in details.tags:
        sources[f"tag.{tag['tag_id']}"] = f"{tag['name']}：{tag['value']}"
    for document in details.documents:
        sources[f"doc.{document['doc_id']}"] = document["content"]
    tag_evidence = {f"tag.{tag['tag_id']}": tag["evidence"] for tag in details.tags}

    draft = None
    try:
        backend = model if model is not None else OpenAICompatibleClient.from_env()
        section_sources = {
            **EXPLANATION_SECTIONS,
            "responsibilities": ("job.description", "tag.T012", "tag.T011"),
        }
        # 让模型专注于每段的核心资料，知识库仍可用来补充细节。
        core_sources = {key for keys in section_sources.values() for key in keys}
        selected_sources = {
            key: fact for key, fact in sources.items() if key in core_sources or key.startswith("doc.")
        }
        payload = json.dumps({
            "sources": selected_sources,
            "section_sources": {section: [key for key in keys if key in selected_sources]
                                for section, keys in section_sources.items()},
            "tag_evidence": {key: evidence for key, evidence in tag_evidence.items() if key in selected_sources},
            "document_limits": {f"doc.{doc['doc_id']}": doc["usage_limit"] for doc in details.documents},
            "writing_phrases": {key: [phrase for phrase in fact_phrases(key, fact, fact)
                                      if not PROHIBITED_CLAIMS.search(phrase)]
                                for key, fact in selected_sources.items() if not PROHIBITED_CLAIMS.search(fact)},
        }, ensure_ascii=False)
        draft = ExplanationDraft.model_validate_json(backend.complete_json(EXPLANATION_PROMPT, payload))
    except (httpx.HTTPError, ValueError, KeyError) as error:
        # 记录可用于排查的错误分类和状态，避免把模型正文或请求数据写入日志。
        cause = type(error).__name__
        if isinstance(error, httpx.HTTPStatusError):
            cause += f" HTTP {error.response.status_code}"
        elif isinstance(error, ValidationError):
            error_types = {item["type"] for item in error.errors(include_input=False, include_context=False)}
            cause += " " + ",".join(sorted(error_types))
        logger.warning("岗位讲解回退，岗位=%r，原因=%s", job["job_id"], cause)

    sections = {}
    used_model = False
    for section, defaults in EXPLANATION_SECTIONS.items():
        proposed = None
        if draft is not None and getattr(draft, section) is not None:
            # 分别校验每段结构，保留其他已经合格的正文和引用。
            try:
                proposed = ExplanationSectionDraft.model_validate(getattr(draft, section))
            except ValidationError as error:
                logger.warning("岗位讲解段落回退，岗位=%r，段落=%s，原因=%s", job["job_id"], section,
                               ",".join(sorted({item["type"] for item in error.errors(include_input=False)})))
        if proposed is not None:
            text = proposed.text.strip()
            evidence = [SourceQuote(source_id=item.source_id, quote=item.quote.strip()) for item in proposed.evidence]
            # 标签值和同一标签的事实依据都是可引用的原文。错引任何一条都让该段回退。
            if text and evidence and not PROHIBITED_CLAIMS.search(text) and all(
                item.quote
                and (item.quote in sources.get(item.source_id, "") or item.quote in (tag_evidence.get(item.source_id) or ""))
                and not PROHIBITED_CLAIMS.search(item.quote)
                for item in evidence
            ):
                # 标签引用中的数字还须出现在标签值中，避免把 Excel 行号当成经验年限等岗位事实。
                cited_numbers = {
                    number
                    for item in evidence
                    for number in EXPLANATION_NUMBERS.findall(item.quote)
                    if item.source_id not in tag_evidence or number in EXPLANATION_NUMBERS.findall(sources[item.source_id])
                }
                phrases = [
                    phrase for item in evidence
                    for phrase in fact_phrases(item.source_id, sources[item.source_id], item.quote, tag_evidence.get(item.source_id))
                ]
                if set(EXPLANATION_NUMBERS.findall(text)) <= cited_numbers and explanation_is_grounded(text, phrases):
                    sections[section] = ExplanationSection(text, evidence)
                    used_model = True
                    continue

        # 模型不可用或该段校验失败，只用可信字段写自然句，并保留原文来源。
        selected = []
        phrases = []
        for key in defaults:
            if (fact := sources.get(key)) and not PROHIBITED_CLAIMS.search(fact):
                options = fact_phrases(key, fact, fact)
                if options:
                    selected.append(SourceQuote(source_id=key, quote=fact))
                    phrases.append(options[0])
        text = "。".join(dict.fromkeys(phrases))
        sections[section] = ExplanationSection(f"{text}。" if text else MISSING_INFORMATION, selected)

    if draft is not None and not used_model:
        logger.warning("岗位讲解回退，岗位=%r，所有改写段落的正文或引用未通过校验", job["job_id"])

    missing = []
    for field, title in (("job.description", "工作内容"), ("tag.T003", "出差频率"), ("tag.T008", "收入相对水平")):
        if field not in sources:
            missing.append(f"{title}：{MISSING_INFORMATION}")
    if not details.documents:
        missing.append(f"岗位知识库资料：{MISSING_INFORMATION}")
    name = job["name"]
    return JobExplanation(
        job_id=job["job_id"], job_name=name if name and not PROHIBITED_CLAIMS.search(name) else MISSING_INFORMATION,
        missing_information=missing, generated_by="llm" if used_model else "fallback", **sections,
    )


# 统计尚未生效标签在候选岗位中的已知取值，按覆盖率和区分度选出最多两个追问方向。
def rank_follow_up_targets(
    profile: Profile, candidate_values: list[dict[str, str]], dictionary: dict[str, TagDefinition],
) -> list[FollowUpTarget]:
    if not candidate_values:
        return []
    observations = defaultdict(Counter)
    for values in candidate_values:
        for tag_id, value in values.items():
            definition = dictionary.get(tag_id)
            if definition and definition.match_rule != "资格参考" and tag_id not in profile.preferences:
                if value in definition.allowed_values:
                    observations[tag_id][value] += 1
    targets = []
    for tag_id, frequencies in observations.items():
        # 至少要有两种有效取值才值得追问，缺失值不作为独立取值参与比较。
        if len(frequencies) < 2:
            continue
        known = sum(frequencies.values())
        # 有值岗位占比乘以已知取值的基尼不纯度，使用分数避免同分排序受浮点误差影响。
        # 取值差异相同时，资料覆盖更多候选岗位的标签优先。
        score = Fraction(known ** 2 - sum(count ** 2 for count in frequencies.values()), len(candidate_values) * known)
        targets.append(FollowUpTarget(tag_id, dictionary[tag_id].name, score, dict(sorted(frequencies.items())), known, len(candidate_values)))
    # 区分度相同时按标签 ID 排序，让相同候选集得到稳定的追问顺序。
    return sorted(targets, key=lambda target: (-target.distinction_score, target.tag_id))[:2]


# 从画像和匹配结果确定追问标签，用该标签的已知选项构造问句。
def follow_up_questions(profile: Profile, matches: list[MatchResult]) -> list[FollowUpQuestion]:
    if not matches:
        return []
    values = {match.job_id: {} for match in matches}
    with connect() as conn:
        dictionary = load_dictionary(conn)
        for job_id, tag_id, value in conn.execute(
            "SELECT job_id, tag_id, value FROM job_tags WHERE job_id = ANY(%s)", (list(values),),
        ):
            if tag_id not in {"T001", "T002"}:
                values[job_id][tag_id] = value
    for match in matches:
        if match.city is not None:
            values[match.job_id]["T001"] = match.city
        if match.direction is not None:
            values[match.job_id]["T002"] = match.direction
    targets = rank_follow_up_targets(profile, list(values.values()), dictionary)
    # 问句由已选标签和字典值组合，避免标签与问题内容不一致。
    questions = []
    for target in targets:
        options = "、".join(target.counts)
        suggestion = profile.pending_suggestions.get(target.tag_id)
        text = (f"关于{target.tag_name}，您希望避开「{suggestion.value['avoid']}」的岗位吗？" if suggestion
                else f"在{target.tag_name}方面，目前候选涉及{options}，您更倾向哪些情况？")
        questions.append(FollowUpQuestion(
            target.tag_id, target.tag_name, text, float(target.distinction_score),
            target.known_jobs, target.candidate_jobs,
        ))
    return questions
