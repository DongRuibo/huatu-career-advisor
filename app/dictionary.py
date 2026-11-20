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


def load_dictionary(conn: Connection) -> dict[str, TagDefinition]:
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT tag_id, name, category, value_mode, allowed_values, ordered, match_rule, unknown_rule "
            "FROM tag_definitions ORDER BY tag_id"
        )
        return {row["tag_id"]: TagDefinition(**row) for row in cursor}
