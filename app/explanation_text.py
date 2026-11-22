# 岗位讲解的事实表达规则。根据真实来源生成可读短句，并校验模型正文是否只包含这些表达。
# 模型可以选择引用、排列与连接句子。涉及地点、职责和资格的实际取值由岗位资料确定。

import re

SENTENCE_END = re.compile(r"[。！？!?；;\n]+")
SPACE = re.compile(r"\s+")
JOB_FORMS = {
    "job.name": ("这个岗位叫{value}", "岗位名称是{value}"),
    "job.city": ("工作地点在{value}", "这份工作在{value}", "这份工作的地点在{value}", "岗位位于{value}"),
    "job.direction": ("岗位属于{value}方向", "属于{value}方向"),
    "job.unit_type": ("所在单位是{value}", "单位类型为{value}", "单位类型是{value}"),
    "job.unit_name": ("所在单位为{value}",),
    "job.education_req": ("学历方面可参考{value}要求", "学历参考要求为{value}"),
    "job.experience_years": ("工作经验方面可参考{value}的要求", "工作经验参考要求为{value}", "该岗位的工作经验参考要求为{value}"),
    "job.political_req": ("政治面貌参考要求为{value}",),
    "job.major_req": ("专业方面可参考{value}",),
    "job.remark": ("资料备注为{value}",),
    "job.updated_at": ("岗位资料更新于{value}",),
}
TAG_FIELDS = {
    "tag.T001": "job.city", "tag.T002": "job.direction", "tag.T017": "job.education_req",
    "tag.T018": "job.experience_years", "tag.T019": "job.political_req", "tag.T020": "job.major_req",
}


# 从一个已核验的来源构造自然句式。知识库只使用引用覆盖的完整句子，保留否定等上下文。
def fact_phrases(source_id: str, source: str, quote: str, original_tag_evidence: str | None = None) -> list[str]:
    if source_id.startswith("doc."):
        sentences = [sentence.strip() for sentence in SENTENCE_END.split(source) if sentence.strip()]
        phrases = [sentence for sentence in sentences if sentence in quote]
        phrases += [
            f"这份工作包括跟进{sentence.removeprefix('需要对接')}的对接"
            for sentence in phrases if sentence.startswith("需要对接")
        ]
        return phrases

    label, _, value = source.partition("：")
    value = value.strip().rstrip("。")
    if not value or (value not in quote and (not original_tag_evidence or quote != original_tag_evidence)):
        return []
    if source_id == "job.description":
        duties = value.removeprefix("负责")
        return [f"这份工作包括{duties}", f"日常工作包括{duties}"]
    field = TAG_FIELDS.get(source_id, source_id)
    if field == "job.experience_years" and not value.endswith("年"):
        value += "年"
    if field in JOB_FORMS:
        return [form.format(value=value) for form in JOB_FORMS[field]]
    if source_id == "tag.T003":
        wording = {"无": "这个岗位无需出差", "偶尔": "这个岗位偶尔需要出差", "频繁": "这个岗位需要频繁出差"}
        return [wording[value], f"按现有资料，{wording[value]}"] if value in wording else []
    if source_id == "tag.T007":
        return [f"岗位采用{value}办公", f"岗位的办公方式为{value}"]
    if source_id == "tag.T013":
        wording = {"少": "这个岗位的加班情况较少", "偶尔": "这个岗位偶尔需要加班", "经常": "这个岗位经常需要加班"}
        return [wording[value]] if value in wording else []
    return [f"{label}为{value}"]


# 验证每个句子都由已引用事实的短句组成，允许句号、逗号和有限连接词。
def explanation_is_grounded(text: str, phrases: list[str]) -> bool:
    candidates = {SPACE.sub("", phrase) for phrase in phrases if phrase}
    if not candidates:
        return False
    for sentence in SENTENCE_END.split(SPACE.sub("", text)):
        sentence = sentence.strip("，,").removeprefix("按现有资料，").removeprefix("按现有资料,")
        if not sentence or sentence in candidates:
            continue
        if any(part.removeprefix("并且").removeprefix("同时") not in candidates for part in re.split("[，,]", sentence)):
            return False
    return bool(text.strip("。！？!?；;，, "))
