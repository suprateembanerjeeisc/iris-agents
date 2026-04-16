import json
from .utils import get_connection, ensure_schema


class Chat:
    def __init__(
        self,
        name: str,
        messages: list[dict[str, str]] | None = None,
        limit: int = 200,
        workflow: str | None = None,
    ):

        self.id = str(name)
        self.workflow = self._normalize_workflow(workflow)
        ensure_schema('Chat')

        conn = get_connection()
        cur = conn.cursor()

        if messages is not None:
            delete_sql = 'DELETE FROM Chat WHERE id = ?'
            delete_params: list[str] = [self.id]

            if self.workflow:
                delete_sql += ' AND workflow = ?'
                delete_params.append(self.workflow)

            cur.execute(delete_sql, tuple(delete_params))
            conn.commit()

            for msg in messages:
                message_role = msg.get('role')
                content = msg.get('content')
                if message_role is None or content is None:
                    raise KeyError('Each message must be {\'role\': ..., \'content\': ...}')

                cur.execute(
                    'INSERT INTO Chat (id, workflow, message_role, message) VALUES (?, ?, ?, ?)',
                    (self.id, self.workflow, message_role, content),
                )
            conn.commit()

        if limit is None or int(limit) <= 0:
            limit = 200
        limit = int(limit)

        select_sql = f'''
            SELECT TOP {limit} message_role, message
            FROM SQLUser.Chat
            WHERE id = ?
        '''
        select_params: list[str] = [self.id]

        if self.workflow:
            select_sql += ' AND workflow = ?'
            select_params.append(self.workflow)

        select_sql += '''
            ORDER BY message_id DESC
        '''

        cur.execute(select_sql, tuple(select_params))
        rows = list(cur.fetchall())
        rows.reverse()

        self.messages = [{'role': row[0], 'content': row[1]} for row in rows]

    @staticmethod
    def _normalize_workflow(workflow: str | None) -> str:
        if workflow is None:
            return ''
        if not isinstance(workflow, str):
            raise TypeError('workflow must be str | None')
        return workflow.strip()

    def usage(self, workflow: str | None = None) -> str:
        ensure_schema('Usage')
        conn = get_connection()
        cur = conn.cursor()

        workflow_value = self.workflow if workflow is None else self._normalize_workflow(workflow)

        sql = '''
            SELECT
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(output_reasoning_tokens), 0) AS output_reasoning_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM SQLUser.Usage
            WHERE chat_id = ?
        '''
        params: list[str] = [self.id]

        if workflow_value:
            sql += ' AND workflow = ?'
            params.append(workflow_value)

        cur.execute(sql, tuple(params))

        row = cur.fetchone()

        result = {
            'input_tokens': int(row[0] or 0),
            'output_tokens': int(row[1] or 0),
            'output_reasoning_tokens': int(row[2] or 0),
            'total_tokens': int(row[3] or 0),
        }

        return json.dumps(result)

    def append(
        self,
        role: str,
        content: str,
        reasoning_summary: str = '',
        reasoning_detailed: str = '',
        workflow: str | None = None,
    ) -> None:
        conn = get_connection()
        cur = conn.cursor()

        workflow_value = self.workflow if workflow is None else self._normalize_workflow(workflow)

        cur.execute(
            '''
            INSERT INTO Chat (id, workflow, message_role, message, reasoning_summary, reasoning_detailed)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (self.id, workflow_value, role, content, reasoning_summary, reasoning_detailed),
        )
        conn.commit()

        if not self.workflow or workflow_value == self.workflow:
            self.messages.append({'role': role, 'content': content})

    def to_json(self) -> str:
        return json.dumps(self.messages, ensure_ascii=False)

    def __repr__(self) -> str:
        workflow = f', workflow={self.workflow!r}' if self.workflow else ''
        return f'Chat(name={self.id!r}{workflow}, messages={len(self.messages)})'

    def __eq__(self, other) -> bool:
        if not isinstance(other, Chat):
            return NotImplemented
        return (
            self.id == other.id
            and self.workflow == other.workflow
            and self.messages == other.messages
        )
