from dotenv import load_dotenv
import string
from .utils import ensure_agents_namespace, get_connection, ensure_schema

load_dotenv()


class Prompt:
    def __init__(self, name: str, text: str | None = None, version: int | None = None):
        if text is not None:
            ensure_agents_namespace()

        ensure_schema('Prompt')
        conn = get_connection()
        cur = conn.cursor()

        # Fetch prompt rows
        if version is None:
            cur.execute(
                '''SELECT prompt_id, prompt_text, version
                   FROM Prompt
                   WHERE prompt_id = ?
                   ORDER BY version DESC''',
                (name,),
            )
        else:
            cur.execute(
                '''SELECT prompt_id, prompt_text, version
                   FROM Prompt
                   WHERE prompt_id = ? AND version = ?''',
                (name, version),
            )

        rows = cur.fetchall()

        last_text = None
        current_version = 0

        if rows:
            fetched_name, last_text, current_version = rows[0]

        self.name = name
        self.text = last_text
        self.version = current_version

        # Fetch-only mode
        if text is None:
            if not rows:
                if version is None:
                    raise ValueError(f"No prompt found for '{name}'")
                raise ValueError(f"No prompt found for '{name}' with version {version}")
            return

        # If a version was explicitly requested together with text, treat that as fetch-only validation.
        # Do not create a new version in that case.
        if version is not None:
            if not rows:
                raise ValueError(f"No prompt found for '{name}' with version {version}")
            if last_text != text:
                raise ValueError(
                    f"Prompt '{name}' version {version} exists, but its text does not match the provided text."
                )
            return

        # No explicit version: either reuse matching existing text or create a new version
        for prompt_id, prompt_text, prompt_version in rows:
            if prompt_text == text:
                self.name = prompt_id
                self.text = prompt_text
                self.version = prompt_version
                return

        new_version = current_version + 1
        cur.execute(
            '''INSERT INTO Prompt (prompt_id, prompt_text, version) VALUES (?, ?, ?)''',
            (name, text, new_version),
        )
        conn.commit()

        self.name = name
        self.text = text
        self.version = new_version

    def __repr__(self) -> str:
        return f'Prompt(name={self.name!r}, version={self.version}, text={self.text!r})'

    def __str__(self) -> str:
        return self.text or ''

    def __eq__(self, other):
        if not isinstance(other, Prompt):
            return NotImplemented
        return (self.name, self.version, self.text) == (other.name, other.version, other.text)

    def __hash__(self):
        return hash((self.name, self.version, self.text))

    def get_variables(self) -> list[str]:
        if not self.text:
            return []

        variables: list[str] = []
        seen = set()

        for _, field_name, _, _ in string.Formatter().parse(self.text):
            if field_name and field_name not in seen:
                seen.add(field_name)
                variables.append(field_name)

        return variables

    def build(self, **vars) -> str:
        if not self.text:
            raise KeyError(f"No prompt text found for '{self.name}'")

        required = set(self.get_variables())
        missing = required - vars.keys()
        if missing:
            raise KeyError(f'Missing variables {sorted(missing)} for the selected prompt')

        return self.text.format(**vars)
    
    def delete(self) -> None:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute('DELETE FROM Prompt WHERE prompt_id = ?', (self.name,))
        conn.commit()

        self.text = None
        self.version = 0
