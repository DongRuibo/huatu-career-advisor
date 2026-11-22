# 从数据库加载标签字典，并以标签 ID 为键提供名称、允许值和匹配规则。
# 抽取校验、手动编辑和匹配共用这里的定义，允许值的顺序对应有序标签的量级。

from dataclasses import dataclass

from psycopg import Connection
from psycopg.rows import dict_row


@dataclass(frozen=True)
class TagDefinition:
    tag_id: str
    name: str
    category: str
    value_mode: str
    allowed_values: list[str]
    ordered: bool
    match_rule: str
    unknown_rule: str


# 加载当前标签字典并按标签 ID 返回类型化对象，保留有序标签的允许值次序。
def load_dictionary(conn: Connection) -> dict[str, TagDefinition]:
    # 按标签 ID 建立索引。保持数据库数组中的取值顺序，供出差上限等规则比较量级。
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT tag_id, name, category, value_mode, allowed_values, ordered, match_rule, unknown_rule "
            "FROM tag_definitions ORDER BY tag_id"
        )
        return {row["tag_id"]: TagDefinition(**row) for row in cursor}
