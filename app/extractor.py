# 把当前咨询发言转换为结构化偏好提议和当前资格。
# 模型结合近期对话理解指代，程序逐条校验标签值、约束、发言角色和本轮原话。
# 输出仅供画像层合并。间接推断保留为待确认建议，资格通过单独字段返回。

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Literal, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.dictionary import TagDefinition
from app.llm import JsonModel, OpenAICompatibleClient

logger = logging.getLogger(__name__)

type Speaker = Literal["家长", "学生", "咨询师"]
type Strength = Literal["hard", "soft"]
type ConstraintType = Literal["eq", "in", "max", "min", "prefer"]
type Action = Literal["set", "add", "remove"]


class OvertimePreference(TypedDict):
    preferred: str
    acceptable_max: str


class TentativeAvoidance(TypedDict):
    avoid: str
    needs_confirmation: Literal[True]


type PreferenceValue = str | list[str] | OvertimePreference | TentativeAvoidance


class Qualifications(TypedDict, total=False):
    education: str
    political_status: str
    experience_years: int


class TurnInput(BaseModel):
    speaker: Speaker
    text: str

    # 在接口入口拒绝空白发言，返回原字符串以便后续证据校验保留原话。
    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("发言不能为空")
        return value


class PreferenceProposal(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    tag_id: str
    value: PreferenceValue | None
    strength: Strength
    constraint_type: ConstraintType
    action: Action
    evidence: str
    speaker: Speaker


class ModelOutput(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)

    preferences: list[object]
    qualifications: dict[str, object] = Field(default_factory=dict)
    qualification_evidence: dict[str, object] = Field(default_factory=dict)


@dataclass(frozen=True)
class Extraction:
    preferences: list[PreferenceProposal]
    qualifications: Qualifications


# 这里列出允许设为硬条件的标签及约束。模型根据发言是否明确限定来提议强度。
HARD_CONSTRAINTS = {"T001": "in", "T002": "in", "T003": "max"}
SUGGESTION_TAGS = {"T007", "T012"}
QUALIFICATION_FIELDS = set(Qualifications.__annotations__)
CHINESE_DIGITS = "零一二三四五六七八九"
CLAUSE_BREAK = re.compile(r"[，。！？；,!?;\n]|但是|不过|但")
HARD_WORDS = re.compile(r"只(?:能|接受|考虑|选)|仅(?:限|考虑|接受)|必须|一定要|限定|最多|不超过|不接受|不能")
SOFT_WORDS = re.compile(r"希望|尽量|最好|优先|倾向|不是必须|不一定|并非|不必|未必|不限定|不只|不限于|不需要|可以讨论")
EXCLUDED = re.compile(r"不(?:再|想|愿意)?考虑|不(?:再|想|愿意|要|能)?去|不接受|排除|移除|取消|去掉|删掉")
OPTION_REMOVAL = re.compile(
    rf"{EXCLUDED.pattern}|删(?:除|去)?|划掉|拿掉|剔除|撤下|不要了|别(?:再)?考虑|不(?:再|用再)?(?:保留|列入|放进|放在|留在)"
)
UNCERTAIN_REMOVAL = re.compile(r"如果|假设|是否|要不要|不是|并非|没说|别\s*急着")
NEGATED_REMOVAL = re.compile(r"(?:别|不要|不用|不必|不能|不想|不打算|不需要)(?:再|先)?(?:把[^，。]{0,18})?(?:从[^，。]{0,10})?$")
CHOICE_WORDS = re.compile(r"考虑|允许|接受|选择|希望|愿意|想|倾向|优先|最好|首选|可以|行$|也行|都行|都好")
NO_TRAVEL = re.compile(r"(?:完全)?(?:不能|不允许|不接受|不希望|不想|不愿意)(?:有任何)?出差|不出差|无需出差|出差频率(?:为|是|[:：])无")
OCCASIONAL_TRAVEL = re.compile(
    r"(?:不(?:希望|想|接受|允许|能)?|别)(?:经常|频繁)(?:跨城市|跨城)?出差"
    r"|偶尔(?:可以|能)?(?:跨城)?出差|出差(?:频率)?(?:为|是|最多|不超过|[:：])?偶尔"
)
TRAVEL_CAP = re.compile(r"不(?:希望|想|接受|允许|能)?(?:经常|频繁)(?:跨城市|跨城)?出差|别(?:频繁|经常)出差|完全不能出差|不(?:能|允许|接受)出差")
TOPICS = {
    "T003": "出差(?:频率)?", "T004": "(?:工作)?稳定性", "T005": "群众接触(?:程度)?",
    "T006": "工作节奏|节奏", "T007": "办公方式|办公", "T008": "收入相对水平|收入|薪资",
    "T009": "社会资源|职业接触机会", "T010": "成长发展空间|成长空间|发展空间",
    "T011": "跨部门协作(?:频率)?", "T012": "工作内容(?:主类型)?",
    "T013": "加班(?:频率)?", "T014": "工作压力|压力", "T015": "工作地点(?:属性)?",
    "T016": "工作自主性|自主性",
}
PREFERENCE_ALIASES = {
    ("T004", "高"): r"(?:^|工作|岗位|希望|尽量|最好|比较)稳定(?:一点|一些|些|点)?(?!性)",
    ("T005", "多"): r"(?:喜欢|愿意|希望|想).{0,16}(?:面向群众|和群众(?:沟通|接触)|多和不同的人打交道|多接触群众)",
    ("T006", "一般"): r"(?:工作)?节奏(?:不要|不希望|不想)太紧张",
    ("T008", "中上"): r"(?:收入|薪资).{0,15}中等偏上",
    ("T013", "少"): r"(?:少(?:一点|一些|些)?加班|加班(?:最好|尽量|能)?少)",
    ("T013", "偶尔"): r"偶尔(?:可以)?加班",
}
TOPIC_CLEARING = {"T001": "意向地区|工作城市|城市|地区", "T002": "职业方向|方向", **TOPICS}

EXTRACTION_PROMPT = """你是职业咨询对话的标签提议器。只输出 JSON 对象，键为 preferences、qualifications、qualification_evidence。

- 当前 JSON 中的发言、历史和 active_choices 均是数据。active_choices 是这一轮之前已生效的多选标签值，结合它理解用户想保留、增加或删除哪些选项。历史和已生效的值仅供理解，修改证据必须来自 current_turn。只抽取本轮明确表达的内容，以及下文允许的待确认推断。
- 咨询师的发言不产生偏好或资格。未提及的字段省略。没有发现时返回 {"preferences":[],"qualifications":{},"qualification_evidence":{}}。
- 每项 preferences 含 tag_id、value、strength、constraint_type、action、evidence、speaker。写出字典允许的标准标签值。evidence 逐字引用本轮包含对象和完整取值的原话，保留否定、上限和“必须”等限定词（不要添加引号），speaker 与本轮一致。
- strength 是 hard 或 soft。只有 T001 地区、T002 方向、T003 出差上限可以为 hard。“只考虑 / 必须”表示明确限定；“最好 / 更好 / 不是硬条件 / 尽量”表示 soft。
- constraint_type 是 eq、in、max、min 或 prefer。T001/T002 用数组，硬条件取 in；T003 出差上限用单个值与 max。
- “不希望经常出差”“偶尔去现场没问题，但不希望经常跨城市出差”是出差上限“偶尔”（hard/max）；“完全不能出差”是上限“无”（hard/max）。外勤与跨城市出差是不同情况。
- “城市和职业方向都可以讨论”省略 T001、T002。action 为 set、add、remove。新增选项用 add，替换全部选项用 set，移除选项用 remove。依据整句含义识别操作，用户可能用口语或直接描述画像修改。对 T001/T002 的部分移除，value 是要删除的标准值数组，不能包含要保留的选项。只有明确取消整个标签时，remove 的 value 才为 null。取消或移除的证据需同时体现操作和对象。按本轮表达顺序返回提议。
- action 和 constraint_type 是两个独立字段。追加城市时 action 是 add，constraint_type 仍是 in。家长说“重庆也可以考虑”时完整示例：{"preferences":[{"tag_id":"T001","value":["重庆"],"strength":"soft","constraint_type":"in","action":"add","evidence":"重庆也可以考虑","speaker":"家长"}],"qualifications":{},"qualification_evidence":{}}。
- “尽量稳定”是 T004 soft/prefer “高”。“喜欢和群众沟通/面向群众做咨询、解释/希望多和不同的人打交道”是 T005 soft/prefer “多”。“工作节奏不要太紧张”可用 T006 soft/max “一般”。
- “加班最好少一些，偶尔可以接受”是 T013 soft/prefer，value 为 {"preferred":"少","acceptable_max":"偶尔"}。若只说首选，value 用单个值。只有明确说了可接受上限才填 acceptable_max。
- “不喜欢天天埋头整理资料”可提议 T007 value={"avoid":"办公室","needs_confirmation":true} 和 T012 value={"avoid":"材料管理","needs_confirmation":true}，都用 soft/prefer。表示倾向并待确认，不能视为禁止办公室或材料工作。仅在本轮有对应原话依据时提议。
- 对 T007 和 T012，只在本轮明确说出偏好的标签值时生成正式偏好；间接推断只提议待确认建议。T012 明确偏好多个工作类型时，用值数组和 soft/prefer/set，原话需覆盖所有选项。
- “在成长发展空间方面，我比较倾向于平稳”是 T010 soft/prefer “平稳”。引用时保留主题和回答。
- “收入中上”是 T008 soft/prefer “中上”，是相对水平，较高也可以接受。
- 当前学历、政治面貌、工作经验只进入 qualifications，键为 education、political_status、experience_years。仅写学生已明确拥有的条件，省略职业目标、岗位要求、未来准备和假设中的资格。每个字段必须同时在 qualification_evidence 中附有单独的本轮原话片段，体现这是谁的当前条件。
- 学历和政治面貌保留原话用词。工作经验为非负整数；“还没有工作经验”是 0。经验的证据需要覆盖表达当前工作经验年数的原话，不能只截取一个数字。
"""


# 检查模型引用能否在本轮发言中定位，定位成功后返回原文片段。
def grounded_quote(evidence: object, text: str) -> str | None:
    if not isinstance(evidence, str):
        return None
    # 去掉空白后匹配，同时保留原始下标，便于返回实际发言的原样片段。
    fragment = "".join(evidence.split())
    indexed = [(index, char) for index, char in enumerate(text) if not char.isspace()]
    position = "".join(char for _, char in indexed).find(fragment)
    if not fragment or position < 0:
        return None
    return text[indexed[position][0]:indexed[position + len(fragment) - 1][0] + 1]


# 根据标签字典校验提议值的形状、允许值及能使用的操作和约束。
def valid_preference(proposal: PreferenceProposal, definition: TagDefinition) -> bool:
    value = proposal.value
    if value is None:
        return proposal.action == "remove"

    allowed_values = definition.allowed_values
    if isinstance(value, dict):
        # 结构化值只用于待确认的避开建议或带可接受上限的加班偏好。
        if proposal.action == "add" or proposal.constraint_type != "prefer":
            return False
        if "avoid" in value:
            return proposal.action == "set" and proposal.tag_id in SUGGESTION_TAGS and value["avoid"] in allowed_values
        preferred, maximum = value["preferred"], value["acceptable_max"]
        return (
            proposal.tag_id == "T013" and preferred in allowed_values and maximum in allowed_values
            and allowed_values.index(preferred) <= allowed_values.index(maximum)
        )

    # 岗位主类型仍为单值，用户可把多个感兴趣的工作类型存为一项软偏好。
    if proposal.tag_id == "T012" and isinstance(value, list):
        return (
            bool(value) and all(choice in allowed_values for choice in value)
            and proposal.action == "set" and proposal.constraint_type in {"in", "prefer"}
        )
    # 地区和方向用非空列表表达选项。上下限只对有序标签有意义。
    if definition.value_mode == "多选":
        return (
            isinstance(value, list) and bool(value)
            and all(choice in allowed_values for choice in value)
            and (proposal.action == "remove" or proposal.constraint_type in {"in", "prefer"})
        )
    if not isinstance(value, str) or value not in allowed_values or proposal.action == "add":
        return False
    if proposal.action == "remove":
        return True
    if proposal.constraint_type in {"max", "min"}:
        return definition.ordered
    return proposal.constraint_type in {"eq", "prefer"}


# 定位证据所在的分句，保留同句的否定、限定和紧接其后的出差上限。
def evidence_context(text: str, quote: str, tag_id: str) -> str:
    start = text.find(quote)
    end = start + len(quote)
    before = list(CLAUSE_BREAK.finditer(text[:start]))
    after = CLAUSE_BREAK.search(text, end)
    begin = before[-1].end() if before else 0
    stop = after.start() if after else len(text)
    if after and tag_id == "T003":
        next_clause = re.match(r"[，,]\s*(?:只要|但|不过)?\s*别(?:频繁|经常)出差", text[stop:])
        if next_clause:
            stop += next_clause.end()
    if before and tag_id == "T005" and text[begin:stop].lstrip().startswith("尤其"):
        begin = before[-2].end() if len(before) > 1 else 0
    return "".join(text[begin:stop].split())


# 校验软偏好的标准值及相关话题，短值如「高」须与对应维度同时出现。
def supports_level(tag_id: str, value: str, quote: str, context: str) -> bool:
    # 只在提及目标维度的句子里核对取值，避免把同一轮的其他否定句当成该偏好的否定。
    if tag_id == "T004" and quote == "稳定" and re.search(r"(?:收入|薪资|作息|不)稳定", context):
        return False
    if tag_id == "T005" and "面向群众" in quote:
        alias = PREFERENCE_ALIASES[(tag_id, "多")]
        if value == "多" and re.search(alias, context):
            return True
    alias = PREFERENCE_ALIASES.get((tag_id, value))
    literal = re.escape(value)
    topic = TOPICS.get(tag_id)
    if not topic:
        return False
    if tag_id in SUGGESTION_TAGS and re.search(rf"(?:避免|不(?:想|喜欢|希望|愿意|倾向|偏好)).{{0,8}}{literal}", context):
        return False
    for clause in CLAUSE_BREAK.split(quote):
        if alias and re.search(alias, clause):
            return True
        if re.search(r"不(?:希望|想|接受|允许|喜欢)|不要", clause):
            continue
        if tag_id in SUGGESTION_TAGS or tag_id == "T015":
            if value in clause:
                return True
        elif re.search(rf"(?:{topic}).{{0,14}}{literal}(?!上|等|偏上)|{literal}(?:的)?(?:{topic})", clause):
            return True
    return False


# 按分句核对移除动词和其指向的选项，保留判断所需的否定和保留措辞。
def named_removals(clauses: list[str], allowed_values: list[str]) -> set[str]:
    removed = set()
    for clause in clauses:
        mentioned = {choice for choice in allowed_values if choice in clause}
        if not mentioned or (len(mentioned) > 1 and re.search(r"只|保留|留下", clause)):
            continue
        if any(not NEGATED_REMOVAL.search(clause[:marker.start()]) for marker in OPTION_REMOVAL.finditer(clause)):
            removed.update(mentioned)
    return removed


# 把原话与提议绑定，仅在明确支持值、操作及强度时允许生效。
def checked_preference(
    proposal: PreferenceProposal, quote: str, text: str, definition: TagDefinition,
    active_choices: list[str] | None,
) -> PreferenceProposal | None:
    tag_id, value, action = proposal.tag_id, proposal.value, proposal.action
    evidence = "".join(quote.split())
    context = evidence_context(text, quote, tag_id)
    clauses = CLAUSE_BREAK.split(context)
    topic = TOPIC_CLEARING.get(tag_id, r"(?!)")
    clear = r"不再限定|不限定|不限(?:制)?|不设(?:限|上限)|不作要求|取消|撤销|都可以(?:了)?"
    if action == "remove" and definition.value_mode == "多选":
        # 同时核对引用和原句，避免模型截掉「别」「不是」等会改变操作方向的词。
        if UNCERTAIN_REMOVAL.search(context) or (re.search(r"[？?]", text) and not re.search(r"请|帮我|麻烦", context)):
            return None
        quoted_clauses = CLAUSE_BREAK.split(evidence)
        named = named_removals(quoted_clauses, definition.allowed_values) & named_removals(clauses, definition.allowed_values)
        if isinstance(value, list):
            return proposal if set(value) <= named else None
        # 只有明确取消整个维度才允许保留空值。点名选项时，空值最多恢复一个明确目标。
        if not named and all(
            any(not any(choice in clause for choice in definition.allowed_values)
                and not re.search(r"(?:别|不要|不用|不想).{0,12}(?:取消|撤销|不限)", clause)
                and re.search(rf"(?:{topic}).{{0,12}}(?:{clear})|(?:{clear}).{{0,12}}(?:{topic})", clause)
                for clause in group)
            for group in (quoted_clauses, clauses)
        ):
            return proposal
        if len(named) == 1 and (active_choices is None or named <= set(active_choices)):
            return proposal.model_copy(update={"value": list(named)})
        return None

    if action == "remove" and value is None:
        return proposal if re.search(rf"(?:{topic}).{{0,12}}(?:{clear})|(?:{clear}).{{0,12}}(?:{topic})", evidence) else None

    if isinstance(value, list):
        if re.search(r"如果|假设|是否|想了解", context):
            return None
        for choice in value:
            relevant = [clause for clause in clauses if choice in clause]
            if choice not in evidence or not relevant or any(OPTION_REMOVAL.search(clause) for clause in relevant):
                return None
            if tag_id == "T012" and not supports_level(tag_id, choice, evidence, context):
                return None
        # 强词必须限定对应取值。「成都的岗位必须有食堂」里的「必须」没有限定城市。
        hard_choices = {
            choice for choice in value
            if any((marker := HARD_WORDS.search(clause)) and choice in clause[marker.end():]
                   and not SOFT_WORDS.search(clause) for clause in clauses)
        }
        if action != "remove" and any(
            choice not in hard_choices and not any(CHOICE_WORDS.search(clause) and choice in clause for clause in clauses)
            for choice in value
        ):
            return None
        hard = tag_id in HARD_CONSTRAINTS and action == "set" and hard_choices == set(value)
        strength = "hard" if hard else "soft"
        return proposal.model_copy(update={"strength": strength, "constraint_type": "in" if hard else proposal.constraint_type})

    if action == "remove":
        topic = TOPICS.get(tag_id, r"(?!)")
        return proposal if value in evidence and EXCLUDED.search(evidence) and re.search(topic, evidence) else None

    if isinstance(value, dict) and "avoid" in value:
        # 只保留原规格允许的两类间接建议。用户仍需显式确认后才会生效。
        supported = re.search(r"(?:不太喜欢|不喜欢|不愿意|不想|不希望).{0,12}(?:整理|处理).{0,4}(?:资料|材料)", evidence)
        return proposal.model_copy(update={"strength": "soft"}) if supported and {
            "T007": "办公室", "T012": "材料管理",
        }.get(tag_id) == value["avoid"] else None

    if isinstance(value, dict):
        preferred, maximum = value["preferred"], value["acceptable_max"]
        upper = re.escape(maximum)
        accepted = re.search(rf"{upper}(?:加班)?(?:可以接受|能接受|可接受|没问题)|(?<!不)(?:可以接受|能接受|可接受){upper}", evidence)
        if not (supports_level(tag_id, preferred, evidence, context) and accepted):
            return None
    elif tag_id == "T003":
        agreed = (
            NO_TRAVEL.search(evidence) if value == "无" else
            OCCASIONAL_TRAVEL.search(evidence) if value == "偶尔" else
            supports_level(tag_id, value, evidence, context) and not OCCASIONAL_TRAVEL.search(context)
        )
        if not agreed:
            return None
        if value != "无" and re.search(r"不(?:希望|想|接受|允许|能)偶尔(?:跨城)?出差", context):
            return None
        # 上限必须修饰出差，避免同轮表达的城市、岗位方向等硬条件影响它。
        hard = bool(TRAVEL_CAP.search(context) or re.search(r"出差(?:频率)?(?:最多|不超过)|只(?:能|接受)(?:偶尔|频繁)出差", context))
        return proposal.model_copy(update={"strength": "hard" if hard else "soft", "constraint_type": "max" if hard else proposal.constraint_type})
    elif not supports_level(tag_id, value, evidence, context):
        return None
    return proposal.model_copy(update={"strength": "soft"})


# 检查原话是否支持模型给出的当前资格，经验值需要对应工作语境和明确的年数。
def qualification_matches(field: str, value: object, quote: str) -> bool:
    compact = "".join(quote.split())
    if field != "experience_years":
        return isinstance(value, str) and bool(value.strip()) and "".join(value.split()) in compact
    if type(value) is not int or not 0 <= value <= 2_147_483_647:
        return False
    # 将明确的无经验表述识别为零年，其余情况要求证据同时包含工作语境和年数。
    if value == 0 and re.search(r"(?:没有|没|无)(?:任何|相关)?(?:工作经验|工作经历|工作过)", compact):
        return True
    if not re.search(r"工作|从业|任职|上班|工龄|经验", compact):
        return False

    # 中文数字和阿拉伯数字都可以作为原话里的年数依据。
    forms = [str(value)]
    if value < 10:
        forms.append(CHINESE_DIGITS[value])
        if value == 2:
            forms.append("两")
    elif value < 100:
        tens, units = divmod(value, 10)
        forms.append((CHINESE_DIGITS[tens] if tens > 1 else "") + "十" + (CHINESE_DIGITS[units] if units else ""))
    return bool(re.search(rf"(?<![\d{CHINESE_DIGITS}两十百.])(?:{'|'.join(forms)})(?:个)?年", compact))


# 识别直接回答追问的完整句子。主题、选择措辞和字典取值一起确定偏好。
def explicit_follow_up_answer(turn: TurnInput, dictionary: dict[str, TagDefinition]) -> PreferenceProposal | None:
    text = "".join(turn.text.split()).rstrip("。！!")
    preference = r"(?:我(?:们)?|孩子)?(?:比较|更)?(?:倾向于?|喜欢|偏好|希望)"
    growth = re.fullmatch(rf"(?:在)?(?:{TOPICS['T010']})(?:方面)?[，,：:]?{preference}(.+)", text)
    if growth:
        tag_id, choices = "T010", [growth[1]]
    else:
        work = re.fullmatch(
            rf"(?:(?:在)?(?:{TOPICS['T012']}|工作类型)(?:方面)?[，,：:]?)?{preference}(.+?)"
            r"(?:(?:这样的|这类|类)?(?:工作类型|工作|岗位))?",
            text,
        )
        if not work:
            return None
        tag_id = "T012"
        choices = list(dict.fromkeys(re.split(r"[、，,]|以及|和|或(?:者)?", work[1])))
    definition = dictionary.get(tag_id)
    if definition is None or any(choice not in definition.allowed_values for choice in choices):
        return None
    return PreferenceProposal(
        tag_id=tag_id, value=choices[0] if len(choices) == 1 else choices, strength="soft",
        constraint_type="prefer", action="set", evidence=turn.text.strip(), speaker=turn.speaker,
    )


# 调用模型处理当前发言，在宿主程序校验后返回可合并的提议和资格。咨询师发言直接跳过抽取。
def extract_turn(
    turn: TurnInput,
    context: list[dict[str, str]],
    dictionary: dict[str, TagDefinition],
    model: JsonModel | None = None,
    *,
    active_choices: dict[str, list[str]] | None = None,
) -> Extraction:
    # 咨询师负责提问，其发言直接返回空抽取结果。
    if turn.speaker == "咨询师":
        return Extraction([], {})
    if not dictionary:
        raise ValueError("标签字典为空，请先导入工作簿")
    if model is None:
        model = OpenAICompatibleClient.from_env()

    input_data = json.dumps({
        "current_turn": turn.model_dump(),
        "recent_turns": context,
        "active_choices": active_choices or {},
        "tag_dictionary": [asdict(definition) for definition in dictionary.values()],
    }, ensure_ascii=False)
    # 整体响应结构无效时终止本轮，单条提议无效时只跳过这一条。
    output = ModelOutput.model_validate_json(model.complete_json(EXTRACTION_PROMPT, input_data))

    direct_answer = explicit_follow_up_answer(turn, dictionary)
    preferences = [direct_answer] if direct_answer else []
    for item in output.preferences:
        try:
            proposal = PreferenceProposal.model_validate(item)
        except ValidationError:
            logger.warning("忽略结构无效的偏好提议")
            continue
        # 完整的显式回答可以补足模型漏提或少提的选项，同一标签只合并一次。
        if direct_answer and proposal.tag_id == direct_answer.tag_id:
            continue

        definition = dictionary.get(proposal.tag_id)
        if definition is None or definition.match_rule == "资格参考":
            logger.warning("忽略未知标签或被当作偏好的资格标签")
            continue
        # 历史发言可辅助理解修正，但新增提议的证据必须来自本轮对应角色的原话。
        evidence = grounded_quote(proposal.evidence, turn.text)
        if evidence is None or proposal.speaker != turn.speaker:
            logger.warning("忽略缺少本轮原话或发言角色不符的提议：%s", definition.tag_id)
            continue
        if not valid_preference(proposal, definition):
            logger.warning("忽略标签值或约束类型不合法的提议：%s", definition.tag_id)
            continue
        choices = None if active_choices is None else active_choices.get(proposal.tag_id, [])
        checked = checked_preference(proposal, evidence, turn.text, definition, choices)
        if checked is None:
            logger.warning("忽略原话不支持标签取值或操作的提议：%s", definition.tag_id)
            continue
        preferences.append(checked.model_copy(update={"evidence": evidence}))

    # 单独校验各项当前资格的原话，再交给画像层保存。
    qualifications = {}
    for field, value in output.qualifications.items():
        evidence = grounded_quote(output.qualification_evidence.get(field), turn.text)
        if field not in QUALIFICATION_FIELDS or evidence is None or not qualification_matches(field, value, evidence):
            logger.warning("忽略无效或缺少本轮原话的资格提议")
            continue
        qualifications[field] = value
    return Extraction(preferences, cast(Qualifications, qualifications))
