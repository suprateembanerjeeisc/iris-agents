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
    _UNSET = object()

    def __init__(
        self,
        name: str,
        description: str | None | object = _UNSET,
        system_prompt: Prompt | None | object = _UNSET,
        model: str | None | object = _UNSET,
        toolkits: list[Toolkit] | None | object = _UNSET,
        response_format: type[BaseModel] | None | object = _UNSET,
    ):
        conn = get_connection()
        cur = conn.cursor()

        sql = """SELECT TABLE_NAME
                 FROM INFORMATION_SCHEMA.Tables
                 WHERE TABLE_TYPE='BASE TABLE'
                 AND TABLE_SCHEMA='SQLUser'"""
        cur.execute(sql)
        tables = [row[0] for row in cur.fetchall()]

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

        if "AgentToolkit" not in tables:
            cur.execute(
                """CREATE TABLE AgentToolkit (
                    agent_name VARCHAR(200) NOT NULL,
                    toolkit_id VARCHAR(200) NOT NULL,
                    PRIMARY KEY (agent_name, toolkit_id)
                )"""
            )
            conn.commit()

        cur.execute("SELECT * FROM Agent WHERE agent_name = ?", (name,))
        row = cur.fetchone()

        fetch_only = all(
            value is Agent._UNSET
            for value in (description, system_prompt, model, toolkits, response_format)
        )

        if fetch_only:
            if row is None:
                raise KeyError(f"No Agent found for '{name}'")

            _, description, system_prompt_id, model, response_format = row
            self.name = name
            self.description = description
            self.system_prompt = Prompt(system_prompt_id) if system_prompt_id else None
            self.model = model
            self.response_format = Message(response_format, None, message_type="Response") if response_format else None

            cur.execute(
                "SELECT toolkit_id FROM AgentToolkit WHERE agent_name = ?",
                (self.name,),
            )
            toolkit_rows = cur.fetchall()
            self.toolkits = [Toolkit(toolkit_id) for (toolkit_id,) in toolkit_rows]
            return

        # Only model is required when creating/updating
        if model is Agent._UNSET:
            raise KeyError("When creating/updating an agent, provide at least model.")

        description_value = None if description is Agent._UNSET else description
        system_prompt_id = system_prompt.name if isinstance(system_prompt, Prompt) else None
        toolkit_list = [] if toolkits in (Agent._UNSET, None) else toolkits

        response_message = None
        response_format_name = None
        if response_format not in (Agent._UNSET, None):
            response_message = Message(
                response_format.__name__,
                response_format,
                message_type="Response",
            )
            response_format_name = response_message.name

        if row is None:
            cur.execute(
                """INSERT INTO Agent
                   (agent_name, description, system_prompt_id, model, response_format)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    name,
                    description_value,
                    system_prompt_id,
                    model,
                    response_format_name,
                ),
            )
            conn.commit()
        else:
            cur.execute(
                """UPDATE Agent SET
                    description = ?,
                    system_prompt_id = ?,
                    model = ?,
                    response_format = ?
                   WHERE agent_name = ?""",
                (
                    description_value,
                    system_prompt_id,
                    model,
                    response_format_name,
                    name,
                ),
            )
            conn.commit()

            cur.execute("DELETE FROM AgentToolkit WHERE agent_name = ?", (name,))
            conn.commit()

        self.name = name
        self.description = description_value
        self.system_prompt = Prompt(system_prompt_id) if system_prompt_id else None
        self.model = model
        self.response_format = response_message
        self.toolkits = []

        if toolkit_list:
            self.add_toolkits(toolkit_list)

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
        message: str | None = None,
        chat: Chat | str | None = None,
        response_format: BaseModel | None = None,
    ) -> str:
        irispy = get_connection(True)

        prod_ref = iris.IRISReference("")
        state_ref = iris.IRISReference(0)

        explicit_response_format = response_format

        effective_response_format = (
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

        if isinstance(chat, Chat):
            chat_id = chat.id
        elif isinstance(chat, str):
            chat_id = chat
        elif chat is None:
            chat_id = "default"
        else:
            raise TypeError("chat must be Chat | str | None")

        payload = {
            "message": message,
            "chatId": chat_id,
            "responseType": (
                f"Agents.Message.{effective_response_format.name}"
                if effective_response_format
                else "Agents.Message.Response"
            ),
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

            if isinstance(data, dict):
                if data.get("ChatId", "") == "":
                    data.pop("ChatId", None)
                if data.get("chatId", "") == "":
                    data.pop("chatId", None)

                raw = json.dumps(data)

                using_default_response = (
                    explicit_response_format is None and self.response_format is None
                ) or (
                    effective_response_format is not None
                    and getattr(effective_response_format, "name", "") == "Response"
                )

                if using_default_response:
                    if "message" in data and isinstance(data["message"], str):
                        return data["message"]
                    if "Message" in data and isinstance(data["Message"], str):
                        return data["Message"]

        except json.JSONDecodeError:
            pass

        return raw
    
    def __eq__(self, other):
        if not isinstance(other, Agent):
            return NotImplemented
        return self.name == other.name