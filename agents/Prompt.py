from dotenv import load_dotenv
import pandas as pd
from .utils import get_connection

load_dotenv()

# TODO: Add a version rollback / selection system such that user can specify which version to get

class Prompt:

    def __init__(self, name:str, text:str|None=None):

        conn = get_connection()
        cur = conn.cursor()

        sql = '''SELECT TABLE_SCHEMA, TABLE_NAME from INFORMATION_SCHEMA.Tables WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = 'SQLUser' '''

        if 'Prompt' not in pd.read_sql_query(sql, conn)['TABLE_NAME'].to_list():

            sql = '''CREATE TABLE Prompt (
                prompt_id    VARCHAR(200) NOT NULL,
                prompt_text   VARCHAR(200) NOT NULL,
                version INT NOT NULL,
                PRIMARY KEY (prompt_id, version))'''

            cur.execute(sql)
            conn.commit()

        sql = f'''SELECT * FROM Prompt WHERE prompt_id = '{name}' ORDER BY version DESC'''
        prompt_df = pd.read_sql_query(sql, conn)

        last_text = None
        version = 0

        if prompt_df is not None and len(prompt_df) > 0:
            name, last_text, version = prompt_df.iloc[0].tolist()
        self.name = name
        self.text = last_text
        self.version = version

        if not last_text and not text:
            raise KeyError(f'No prompt text found for \'{name}\', and no \'text\' was provided.')
        
        if text is not None:
            match_df = prompt_df[prompt_df["prompt_text"] == text]

            if len(match_df) > 0:
                matched_name, matched_text, matched_version = match_df.iloc[0].tolist()
                self.name = matched_name
                self.text = matched_text
                self.version = matched_version
            else:
                sql = f"""INSERT INTO Prompt (prompt_id, prompt_text, version)
                          VALUES ('{name}', '{text}', {version + 1})"""
                cur.execute(sql)
                conn.commit()
                self.text = text
                self.version += 1

    def __repr__(self) -> str:
        return f'Prompt(name={self.name!r}, version={self.version}, text={self.text!r})'
    
    def __str__(self) -> str:
        return self.text or ''

    def build(self, **vars) -> str:
        import string
        vars_req = {var for _, var, _, _ in string.Formatter().parse(self.text) if var}
        missing = vars_req - vars.keys()
        if missing:
            raise KeyError(f'Missing variables {sorted(missing)} for the selected prompt')
        return self.text.format(**vars)