import json
from .utils import get_connection, ensure_schema


class Chat:
    def __init__(
        self,
        name: str,
        messages: list[dict[str, str]] | None = None,
        limit: int = 200,
    ):

        self.id = str(name)
        ensure_schema('Chat')

        conn = get_connection()
        cur = conn.cursor()

        if messages is not None:
            delete_sql = 'DELETE FROM Agents.Chat WHERE id = ?'
            delete_params: list[str] = [self.id]

            cur.execute(delete_sql, tuple(delete_params))
            conn.commit()

            for msg in messages:
                message_role = msg.get('role')
                content = msg.get('content')
                if message_role is None or content is None:
                    raise KeyError('Each message must be {\'role\': ..., \'content\': ...}')

                cur.execute(
                    'INSERT INTO Agents.Chat (id, message_role, message) VALUES (?, ?, ?)',
                    (self.id, message_role, content),
                )
            conn.commit()

        if limit is None or int(limit) <= 0:
            limit = 200
        limit = int(limit)

        select_sql = f'''
            SELECT TOP {limit} message_role, message
            FROM Agents.Chat
            WHERE id = ?
            ORDER BY message_id DESC
        '''
        select_params: list[str] = [self.id]

        cur.execute(select_sql, tuple(select_params))
        rows = list(cur.fetchall())
        rows.reverse()

        self.messages = [{'role': row[0], 'content': row[1]} for row in rows]

    def usage(self) -> dict[str, int]:
        ensure_schema('Usage')
        conn = get_connection()
        cur = conn.cursor()

        sql = '''
            SELECT
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(output_reasoning_tokens), 0) AS output_reasoning_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM Agents.Usage
            WHERE chat_id = ?
        '''
        params: list[str] = [self.id]

        cur.execute(sql, tuple(params))

        row = cur.fetchone()

        result = {
            'input_tokens': int(row[0] or 0),
            'output_tokens': int(row[1] or 0),
            'output_reasoning_tokens': int(row[2] or 0),
            'total_tokens': int(row[3] or 0),
        }

        return result

    def append(
        self,
        role: str,
        content: str,
        reasoning_summary: str = '',
        reasoning_detailed: str = '',
    ) -> None:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            '''
            INSERT INTO Agents.Chat (id, message_role, message, reasoning_summary, reasoning_detailed)
            VALUES (?, ?, ?, ?, ?)
            ''',
            (self.id, role, content, reasoning_summary, reasoning_detailed),
        )
        conn.commit()

        self.messages.append({'role': role, 'content': content})

    def to_json(self) -> str:
        return json.dumps(self.messages, ensure_ascii=False)

    def __repr__(self) -> str:
        return f'Chat(name={self.id!r}, messages={len(self.messages)})'

    def __eq__(self, other) -> bool:
        if not isinstance(other, Chat):
            return NotImplemented
        return (
            self.id == other.id
            and self.messages == other.messages
        )
