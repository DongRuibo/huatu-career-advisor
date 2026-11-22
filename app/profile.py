# 管理咨询会话、发言、当前偏好、资格、待确认建议和修正历史。
# 提交一轮时先校验抽取结果，再按覆盖、追加或移除规则合并，并在同一事务中保存。
# 手动修改和显式确认建议是独立入口，匹配引擎使用已生效画像。

from dataclasses import dataclass, field
from typing import Literal, TypedDict, cast
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.db import connect, upsert_rows
from app.dictionary import load_dictionary
from app.extractor import (
    HARD_CONSTRAINTS, SUGGESTION_TAGS, ConstraintType, OvertimePreference, PreferenceProposal, Qualifications, Strength,
    TentativeAvoidance, TurnInput, extract_turn, valid_preference,
)
from app.llm import JsonModel


class SessionNotFoundError(LookupError):
    pass


class SuggestionNotFoundError(LookupError):
    pass


class ConfirmedAvoidance(TypedDict):
    avoid: str
    needs_confirmation: Literal[False]


type ActivePreferenceValue = str | list[str] | OvertimePreference | ConfirmedAvoidance


@dataclass(frozen=True)
class Preference:
    tag_id: str
    value: ActivePreferenceValue
    strength: Strength
    constraint_type: ConstraintType
    evidence_turn_id: int | None
    evidence_text: str | None


@dataclass(frozen=True)
class PreferenceChange:
    tag_id: str
    old_value: ActivePreferenceValue | None
    new_value: ActivePreferenceValue | None
    reason: str
    evidence_turn_id: int | None
    turn_no: int | None


@dataclass(frozen=True)
class PreferenceSuggestion:
    tag_id: str
    value: TentativeAvoidance
    evidence_turn_id: int | None
    evidence_text: str | None


@dataclass(frozen=True)
class Profile:
    preferences: dict[str, Preference]
    qualifications: Qualifications
    changes: list[PreferenceChange]
    pending_suggestions: dict[str, PreferenceSuggestion] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnResult:
    turn_id: int
    turn_no: int
    changes: list[PreferenceChange]
    qualifications: Qualifications
    pending_suggestions: list[PreferenceSuggestion] = field(default_factory=list)


# 将单条有效提议合并为新的正式偏好。撤销时返回空值，无变化时复用原对象。
def merge_preference(
    previous: Preference | None, proposal: PreferenceProposal, turn_id: int
) -> Preference | None:
    # 覆盖采用本轮值和约束。追加、部分移除只改选择范围，保留已有强度与约束。
    value = proposal.value
    if isinstance(value, dict) and "avoid" in value:
        return previous
    strength, constraint_type = proposal.strength, proposal.constraint_type
    if proposal.action == "remove":
        # 空值表示取消整个标签，指定选项则只从已有偏好中移除命中的值。
        if previous is None or value is None:
            return None
        if not isinstance(value, list):
            return None if previous.value == value else previous
        removed = set(value)
        value = [choice for choice in cast(list[str], previous.value) if choice not in removed]
        if not value:
            return None
        strength, constraint_type = previous.strength, previous.constraint_type
    elif proposal.action == "add" and previous is not None:
        # 扩充或缩减已有集合时，沿用该集合的约束强度。
        value = [*cast(list[str], previous.value), *cast(list[str], value)]
        strength, constraint_type = previous.strength, previous.constraint_type

    if isinstance(value, list):
        value = list(dict.fromkeys(value))
    # 值、强度和约束都未变化时复用旧对象，保留此前的证据与修正记录。
    if previous is not None and (value, strength, constraint_type) == (
        previous.value, previous.strength, previous.constraint_type,
    ):
        return previous
    assert value is not None
    return Preference(proposal.tag_id, value, strength, constraint_type, turn_id, proposal.evidence)


# 在数据库写入默认模式的咨询会话，返回数据库生成的会话 UUID。
def create_session() -> UUID:
    with connect() as conn:
        session_id, = conn.execute("INSERT INTO sessions DEFAULT VALUES RETURNING session_id").fetchone()
    return session_id


# 核对调用方连接中是否存在指定会话，缺失时抛出会话未找到错误。
def require_session(conn: Connection, session_id: UUID) -> None:
    if conn.execute("SELECT 1 FROM sessions WHERE session_id = %s", (session_id,)).fetchone() is None:
        raise SessionNotFoundError("会话不存在")


# 按标签 ID 读取一个会话的正式偏好以及证据，供合并和匹配使用。
def read_preferences(conn: Connection, session_id: UUID) -> dict[str, Preference]:
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT tag_id, value, strength, constraint_type, evidence_turn_id, evidence_text "
            "FROM user_preferences WHERE session_id = %s ORDER BY tag_id",
            (session_id,),
        )
        return {row["tag_id"]: Preference(**row) for row in cursor}


# 组合当前偏好、资格、待确认建议和有序修正历史，返回指定会话的画像。
def get_profile(session_id: UUID) -> Profile:
    with connect() as conn:
        require_session(conn, session_id)
        preferences = read_preferences(conn, session_id)
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT education, political_status, experience_years FROM user_qualifications WHERE session_id = %s",
                (session_id,),
            )
            qualifications = {key: value for key, value in (cursor.fetchone() or {}).items() if value is not None}
            # 历史查询排除旧版混入的待确认项。建议确认为正式偏好时，新值仍正常出现在历史中。
            cursor.execute(
                "SELECT c.tag_id, CASE WHEN c.old_value @> '{\"needs_confirmation\": true}'::jsonb "
                "THEN NULL ELSE c.old_value END AS old_value, c.new_value, c.reason, c.evidence_turn_id, t.turn_no "
                "FROM preference_changes c LEFT JOIN dialogue_turns t ON t.id = c.evidence_turn_id "
                "WHERE c.session_id = %s AND NOT COALESCE(c.new_value @> '{\"needs_confirmation\": true}'::jsonb, false) "
                "AND (c.new_value IS NOT NULL OR NOT COALESCE(c.old_value @> '{\"needs_confirmation\": true}'::jsonb, false)) "
                "ORDER BY c.id",
                (session_id,),
            )
            changes = [PreferenceChange(**row) for row in cursor]
            cursor.execute(
                "SELECT tag_id, value, evidence_turn_id, evidence_text FROM preference_suggestions "
                "WHERE session_id = %s ORDER BY tag_id",
                (session_id,),
            )
            suggestions = {row["tag_id"]: PreferenceSuggestion(**row) for row in cursor}
    return Profile(preferences, qualifications, changes, suggestions)


# 使用同一连接更新或删除正式偏好，并追加含前后取值和证据的历史记录。
def save_preference_change(
    conn: Connection, session_id: UUID, updated: Preference | None, change: PreferenceChange,
) -> None:
    # 当前画像和修正历史共用调用方事务，避免只保存其中一份。
    if updated is None:
        conn.execute(
            "DELETE FROM user_preferences WHERE session_id = %s AND tag_id = %s",
            (session_id, change.tag_id),
        )
    else:
        upsert_rows(conn, "user_preferences", ("session_id", "tag_id"), [{
            "session_id": session_id, "tag_id": updated.tag_id, "value": Jsonb(updated.value),
            "strength": updated.strength, "constraint_type": updated.constraint_type,
            "evidence_turn_id": updated.evidence_turn_id, "evidence_text": updated.evidence_text,
        }])
    conn.execute(
        "INSERT INTO preference_changes (session_id, tag_id, old_value, new_value, reason, evidence_turn_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (session_id, change.tag_id, Jsonb(change.old_value) if change.old_value is not None else None,
         Jsonb(change.new_value) if change.new_value is not None else None, change.reason, change.evidence_turn_id),
    )


# 消费指向同一会话和原话轮次的待确认建议，将其转为正式软偏好。
def confirm_suggestion(session_id: UUID, tag_id: str, evidence_turn_id: int) -> PreferenceChange | None:
    with connect() as conn:
        require_session(conn, session_id)
        # 同时核对会话、标签和证据轮次。成功取走建议后再次确认会查不到该记录。
        suggestion = conn.execute(
            "DELETE FROM preference_suggestions s USING dialogue_turns t "
            "WHERE s.session_id = %s AND s.tag_id = %s AND s.evidence_turn_id = %s AND t.id = s.evidence_turn_id "
            "AND t.session_id = s.session_id AND s.value @> '{\"needs_confirmation\": true}'::jsonb "
            "RETURNING s.value, s.evidence_text, t.turn_no",
            (session_id, tag_id, evidence_turn_id),
        ).fetchone()
        if suggestion is None:
            raise SuggestionNotFoundError("该轮次的待确认建议不存在")
        value, evidence_text, turn_no = suggestion
        previous = read_preferences(conn, session_id).get(tag_id)
        confirmed: ConfirmedAvoidance = {"avoid": value["avoid"], "needs_confirmation": False}
        if previous is not None and previous.value == confirmed:
            return None

        updated = Preference(tag_id, confirmed, "soft", "prefer", evidence_turn_id, evidence_text)
        reason = f"用户确认待确认建议，原话：{evidence_text}" if evidence_text else "用户确认待确认建议"
        change = PreferenceChange(
            tag_id, previous.value if previous else None, confirmed, reason, evidence_turn_id, turn_no,
        )
        save_preference_change(conn, session_id, updated, change)
    return change


# 按字典校验手动输入，写入正式画像并记录手动来源。相同值和约束无需追加变更。
def set_manual_preference(
    session_id: UUID, tag_id: str, value: str | list[str] | OvertimePreference,
    strength: Strength, constraint_type: ConstraintType,
) -> PreferenceChange | None:
    with connect() as conn:
        require_session(conn, session_id)
        definition = load_dictionary(conn).get(tag_id)
        proposal = PreferenceProposal(
            tag_id=tag_id, value=value, strength=strength, constraint_type=constraint_type,
            action="set", evidence="", speaker="家长",
        )
        if (
            definition is None or definition.match_rule == "资格参考" or not valid_preference(proposal, definition)
            or (strength == "hard" and HARD_CONSTRAINTS.get(tag_id) != constraint_type)
        ):
            raise ValueError("该标签、取值或约束不符合标签字典和匹配规则")
        if isinstance(value, list):
            value = list(dict.fromkeys(value))
        if tag_id in SUGGESTION_TAGS:
            conn.execute("DELETE FROM preference_suggestions WHERE session_id = %s AND tag_id = %s", (session_id, tag_id))
        previous = read_preferences(conn, session_id).get(tag_id)
        if previous is not None and (previous.value, previous.strength, previous.constraint_type) == (value, strength, constraint_type):
            return None
        # 手动设置没有对应发言，在修正记录中标明来源。
        updated = Preference(tag_id, value, strength, constraint_type, None, None)
        change = PreferenceChange(tag_id, previous.value if previous else None, value, "用户手动修改", None, None)
        save_preference_change(conn, session_id, updated, change)
    return change


# 读取近期发言，完成抽取校验后保存本轮原话、偏好、待确认建议与已识别资格。
def submit_turn(session_id: UUID, turn: TurnInput, model: JsonModel | None = None) -> TurnResult:
    with connect() as conn:
        require_session(conn, session_id)
        with conn.cursor(row_factory=dict_row) as cursor:
            # 只取本会话最近三轮，转回时间正序供模型理解指代。
            cursor.execute(
                "SELECT speaker, text FROM dialogue_turns WHERE session_id = %s ORDER BY turn_no DESC LIMIT 3",
                (session_id,),
            )
            context = list(reversed(cursor.fetchall()))
        # 模型需要当前选项判断用户在修改哪一项，历史发言可能已与实际画像不同。
        active_choices = dict(conn.execute(
            "SELECT tag_id, value FROM user_preferences WHERE session_id = %s AND tag_id IN ('T001', 'T002')",
            (session_id,),
        ))
        # 先抽取和校验再写入发言。模型调用或整体校验失败时，本轮没有新记录。
        extracted = extract_turn(turn, context, load_dictionary(conn), model, active_choices=active_choices)
        turn_id, turn_no = conn.execute(
            "INSERT INTO dialogue_turns (session_id, turn_no, speaker, text) "
            "SELECT %s, COALESCE(MAX(turn_no), 0) + 1, %s, %s FROM dialogue_turns WHERE session_id = %s "
            "RETURNING id, turn_no",
            (session_id, turn.speaker, turn.text, session_id),
        ).fetchone()

        changes = []
        suggestions = {}
        current = read_preferences(conn, session_id)
        for proposal in extracted.preferences:
            value = proposal.value
            if isinstance(value, dict) and "avoid" in value:
                # 间接偏好暂存在建议表，用户显式确认后才进入匹配画像。
                suggestion = PreferenceSuggestion(proposal.tag_id, value, turn_id, proposal.evidence)
                upsert_rows(conn, "preference_suggestions", ("session_id", "tag_id"), [{
                    "session_id": session_id, "tag_id": suggestion.tag_id, "value": Jsonb(suggestion.value),
                    "evidence_turn_id": turn_id, "evidence_text": suggestion.evidence_text,
                }])
                suggestions[proposal.tag_id] = suggestion
                continue
            if proposal.tag_id in SUGGESTION_TAGS:
                # 同一标签有了直接的正式提议时，清理不再需要确认的旧建议。
                conn.execute(
                    "DELETE FROM preference_suggestions WHERE session_id = %s AND tag_id = %s",
                    (session_id, proposal.tag_id),
                )
                suggestions.pop(proposal.tag_id, None)

            previous = current.get(proposal.tag_id)
            updated = merge_preference(previous, proposal, turn_id)
            if updated is previous:
                continue
            if updated is None:
                del current[proposal.tag_id]
            else:
                current[proposal.tag_id] = updated

            old_value = None if previous is None else previous.value
            new_value = None if updated is None else updated.value
            change = PreferenceChange(proposal.tag_id, old_value, new_value, proposal.evidence, turn_id, turn_no)
            save_preference_change(conn, session_id, updated, change)
            changes.append(change)

        # 只写本轮已确认的资格字段，保留此前已知的其他资格。
        if extracted.qualifications:
            upsert_rows(conn, "user_qualifications", ("session_id",), [{
                "session_id": session_id, **extracted.qualifications,
            }])
    return TurnResult(turn_id, turn_no, changes, extracted.qualifications, list(suggestions.values()))
