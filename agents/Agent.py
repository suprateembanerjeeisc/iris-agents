import json
import pandas as pd
import iris
from pydantic import BaseModel
from .utils import get_connection
from .Toolkit import Toolkit
from .Prompt import Prompt
from .Message import Message
from .Chat import Chat


class Agent:
    def __init__(
        self,
        name: str,
        description: str | None = None,
        system_prompt: Prompt | None = None,
        model: str | None = None,
        toolkits: list[Toolkit] | None = None,
        response_format: BaseModel | None = None,
    ):
        conn = get_connection()
        cur = conn.cursor()

        response_message = None
        if response_format is not None:
            response_message = Message(
                response_format.__name__,
                response_format,
                message_type="Response",
            )

        # --- Ensure tables exist
        sql = """SELECT TABLE_NAME
                 FROM INFORMATION_SCHEMA.Tables
                 WHERE TABLE_TYPE='BASE TABLE'
                 AND TABLE_SCHEMA='SQLUser'"""
        tables = pd.read_sql_query(sql, conn)["TABLE_NAME"].to_list()

        if "Agent" not in tables:
            cur.execute(
                """CREATE TABLE Agent (
                    agent_name VARCHAR(200) NOT NULL PRIMARY KEY,
                    description VARCHAR(4000),
                    system_prompt_id VARCHAR(200),
                    model VARCHAR(200),
                    response_format VARCHAR(4000)
                )"""
            )
            conn.commit()

        tables = pd.read_sql_query(sql, conn)["TABLE_NAME"].to_list()

        if "AgentToolkit" not in tables:
            cur.execute(
                """CREATE TABLE AgentToolkit (
                    agent_name VARCHAR(200) NOT NULL,
                    toolkit_id VARCHAR(200) NOT NULL,
                    PRIMARY KEY (agent_name, toolkit_id)
                )"""
            )
            conn.commit()

        # --- Read agent row (if exists)
        agent_df = pd.read_sql_query(
            "SELECT * FROM Agent WHERE agent_name = ?",
            conn,
            params=(name,),
        )

        if agent_df is not None and len(agent_df) > 0:
            row = agent_df.iloc[0]

            system_prompt_id = system_prompt.name if system_prompt else row["system_prompt_id"]

            new_description = description if description is not None else row["description"]
            new_model = model if model is not None else row["model"]
            new_response_format = (
                response_message.name if response_message is not None else row["response_format"]
            )

            cur.execute(
                """UPDATE Agent SET
                    description = ?,
                    system_prompt_id = ?,
                    model = ?,
                    response_format = ?
                   WHERE agent_name = ?""",
                (
                    new_description,
                    system_prompt_id,
                    new_model,
                    new_response_format,
                    name,
                ),
            )
            conn.commit()

            self.name = name
            self.description = new_description
            self.system_prompt = Prompt(system_prompt_id) if system_prompt_id else None
            self.model = new_model
            self.response_format = response_message if response_message is not None else new_response_format

            # Load toolkit state from DB during init
            toolkit_df = pd.read_sql_query(
                "SELECT toolkit_id FROM AgentToolkit WHERE agent_name = ?",
                conn,
                params=(self.name,),
            )
            self.toolkits = []
            if not toolkit_df.empty:
                self.toolkits = [
                    Toolkit(toolkit_id)
                    for toolkit_id in toolkit_df["toolkit_id"].to_list()
                ]

            # Apply any additional toolkits passed into init
            if toolkits:
                self.add_toolkits(toolkits)

            return

        # --- Create new agent
        if any(value is None for value in (description, model)):
            raise KeyError("Missing required fields to create a new agent.")

        system_prompt_id = system_prompt.name if system_prompt else None

        cur.execute(
            """INSERT INTO Agent
               (agent_name, description, system_prompt_id, model, response_format)
               VALUES (?, ?, ?, ?, ?)""",
            (
                name,
                description,
                system_prompt_id,
                model,
                response_message.name if response_message else None,
            ),
        )
        conn.commit()

        self.name = name
        self.description = description
        self.system_prompt = Prompt(system_prompt_id) if system_prompt_id else None
        self.model = model
        self.response_format = response_message
        self.toolkits = []

        if toolkits:
            self.add_toolkits(toolkits)

    def __repr__(self) -> str:
        return (
            f"Agent(name={self.name!r}, model={self.model!r}, "
            f"system_prompt={getattr(self.system_prompt, 'name', None)!r})"
        )

    def add_toolkits(self, toolkits: list[Toolkit]) -> None:
        conn = get_connection()
        cur = conn.cursor()

        existing_ids = {toolkit.name for toolkit in self.toolkits}

        for toolkit in toolkits:
            toolkit_id = toolkit.name

            Toolkit(toolkit_id, getattr(toolkit, "url", None))

            if toolkit_id in existing_ids:
                continue

            cur.execute(
                "INSERT INTO AgentToolkit (agent_name, toolkit_id) VALUES (?, ?)",
                (self.name, toolkit_id),
            )

            self.toolkits.append(toolkit)
            existing_ids.add(toolkit_id)

        conn.commit()

    def remove_toolkits(self, toolkits: list[Toolkit] | list[str]) -> None:
        conn = get_connection()
        cur = conn.cursor()

        toolkit_ids_to_remove = {
            toolkit if isinstance(toolkit, str) else toolkit.name
            for toolkit in toolkits
        }

        for toolkit_id in toolkit_ids_to_remove:
            cur.execute(
                "DELETE FROM AgentToolkit WHERE agent_name = ? AND toolkit_id = ?",
                (self.name, toolkit_id),
            )

        conn.commit()

        self.toolkits = [
            toolkit
            for toolkit in self.toolkits
            if toolkit.name not in toolkit_ids_to_remove
        ]

    def __call__(
        self,
        chat: Chat | str | None = None,
        message: str | None = None,
        response_format: BaseModel | None = None,
    ) -> str:
        irispy = get_connection(True)

        prod_ref = iris.IRISReference("")
        state_ref = iris.IRISReference(0)

        response_format = (
            Message(response_format.__name__, response_format, message_type="Response")
            if response_format
            else self.response_format
        )

        sc = irispy.classMethodValue(
            "Ens.Director", "GetProductionStatus", prod_ref, state_ref, 10, 0
        )
        if sc != 1:
            raise RuntimeError(irispy.classMethodValue("%SYSTEM.Status", "GetErrorText", sc))

        production_name = prod_ref.getValue()
        if not production_name:
            raise RuntimeError("No production is currently running in this namespace.")

        state_text = irispy.classMethodValue(
            "Ens.Config.Production",
            "ProductionStateToText",
            state_ref.getValue(),
            0,
        )
        if state_text != "Running":
            raise RuntimeError(
                f"Production '{production_name}' is not Running (state={state_text})."
            )

        if message is None:
            raise ValueError("Provide message=...")

        chat_id = ""
        if isinstance(chat, Chat):
            chat_id = chat.id
        elif isinstance(chat, str):
            chat_id = chat
        elif chat is not None:
            raise TypeError("chat must be Chat | str | None")

        payload = {
            "message": message,
            "chatId": chat_id,
            "responseType": f"Agents.Message.{response_format.name}" if response_format else "",
        }

        request_object = irispy.classMethodObject("Agents.Message.Request", "%New")
        request_object.invoke("%JSONImport", json.dumps(payload))

        gateway_item = f"{self.name}Gateway"

        service_ref = iris.IRISReference(None)
        sc = irispy.classMethodValue("Ens.Director", "CreateBusinessService", gateway_item, service_ref)
        if sc != 1:
            raise RuntimeError(irispy.classMethodValue("%SYSTEM.Status", "GetErrorText", sc))

        service_object = service_ref.getValue()

        response_ref = iris.IRISReference(None)
        hint_ref = iris.IRISReference("")

        sc = service_object.invoke("ProcessInput", request_object, response_ref, hint_ref)
        if sc != 1:
            raise RuntimeError(irispy.classMethodValue("%SYSTEM.Status", "GetErrorText", sc))

        response_object = response_ref.getValue()
        if response_object is None:
            raise RuntimeError("Gateway returned no response object.")

        out_ref = iris.IRISReference("")
        sc = response_object.invoke("%JSONExportToString", out_ref)
        if sc != 1:
            raise RuntimeError(irispy.classMethodValue("%SYSTEM.Status", "GetErrorText", sc))

        raw = out_ref.getValue()

        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("ChatId", "") == "":
                data.pop("ChatId", None)
                raw = json.dumps(data)
        except json.JSONDecodeError:
            pass

        if response_format is None:
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and "Message" in data:
                    return data["Message"]
            except json.JSONDecodeError:
                pass

        return raw