from fastmcp import FastMCP
import iris
import os
from dotenv import load_dotenv

load_dotenv()
mcp = FastMCP('IRIS')

def get_connection(namespace: str):
    conn = iris.connect(
        hostname=os.environ["IRIS_HOSTNAME"],
        port=int(os.environ["IRIS_PORT"]),
        namespace=namespace,
        username=os.environ["IRIS_USERNAME"],
        password=os.environ["IRIS_PASSWORD"],
    )
    return conn

@mcp.tool()
def list_tables(namespace:str):
    '''Lists all user tables'''
    conn = get_connection(namespace)
    cur = conn.cursor()

    cur.execute("""
        SELECT TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.Tables
        WHERE TABLE_TYPE = 'BASE TABLE'
        AND TABLE_SCHEMA = 'SQLUser'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """)

    rows = cur.fetchall()

    return [f'{row[0]}.{row[1]}' for row in rows]


@mcp.tool()
def query(namespace:str, sql: str):
    '''Executes a SQL statement in a specified IRIS Namespace'''
    conn = get_connection(namespace)
    cur = conn.cursor()

    cur.execute(sql)

    columns = [col[0] for col in cur.description]
    rows = cur.fetchall()

    return [
        {
            columns[i]: "" if value is None else str(value)
            for i, value in enumerate(row)
        }
        for row in rows
    ]


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=9002,
        path="/mcp",
        json_response=True
    )