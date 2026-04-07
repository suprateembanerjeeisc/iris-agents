import json
import pandas as pd
from .utils import get_connection

class Chat:
    def __init__(self,
                 name: str,
                 messages: list[dict[str, str]] | None = None,
                 limit: int = 200):

        self.id = str(name)

        conn = get_connection()
        cur = conn.cursor()

        # Ensure table exists
        sql = """
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.Tables
            WHERE TABLE_TYPE='BASE TABLE'
            AND TABLE_SCHEMA='SQLUser'
        """
        cur.execute(sql)
        tables = [row[0] for row in cur.fetchall()]

        if "Chat" not in tables:
            cur.execute("""
                CREATE TABLE Chat (
                    message_id BIGINT IDENTITY PRIMARY KEY,
                    id VARCHAR(200) NOT NULL,
                    message_role VARCHAR(50) NOT NULL,
                    message VARCHAR(50000) NOT NULL,
                    reasoning_trace VARCHAR(50000)
                )
            """)
            conn.commit()

            try:
                cur.execute("CREATE INDEX idx_chat_id_msgid ON Chat (id, message_id)")
                conn.commit()
            except Exception:
                pass

        # If messages provided: reset + seed
        if messages is not None:
            cur.execute("DELETE FROM Chat WHERE id = ?", (self.id,))
            conn.commit()

            for msg in messages:
                message_role = msg.get("role")
                content = msg.get("content")
                if message_role is None or content is None:
                    raise KeyError("Each message must be {'role': ..., 'content': ...}")

                cur.execute(
                    "INSERT INTO Chat (id, message_role, message) VALUES (?, ?, ?)",
                    (self.id, message_role, content)
                )
            conn.commit()

        # Load history (bounded)
        if limit is None or int(limit) <= 0:
            limit = 200
        limit = int(limit)

        # Pull last N messages efficiently, then reverse to chronological
        cur.execute(
            f"""
            SELECT TOP {limit} message_role, message
            FROM SQLUser.Chat
            WHERE id = ?
            ORDER BY message_id DESC
            """,
            (self.id,)
        )
        rows = list(cur.fetchall())
        rows.reverse()

        self.messages = [{"role": row[0], "content": row[1]} for row in rows]

    def usage(self) -> str:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(output_reasoning_tokens), 0) AS output_reasoning_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM SQLUser.Usage
            WHERE chat_id = ?
            """,
            (self.id,),
        )

        row = cur.fetchone()

        result = {
            "input_tokens": int(row[0] or 0),
            "output_tokens": int(row[1] or 0),
            "output_reasoning_tokens": int(row[2] or 0),
            "total_tokens": int(row[3] or 0),
        }

        return json.dumps(result)

    def append(self, role: str, content: str) -> None:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO Chat (id, message_role, message) VALUES (?, ?, ?)",
            (self.id, role, content)
        )
        conn.commit()

        self.messages.append({"role": role, "content": content})

    def to_json(self) -> str:
        return json.dumps(self.messages, ensure_ascii=False)

    def __repr__(self) -> str:
        return f"Chat(name={self.id!r}, messages={len(self.messages)})"
    
    def __eq__(self, other) -> bool:
        if not isinstance(other, Chat):
            return NotImplemented
        return self.id == other.id and self.messages == other.messages