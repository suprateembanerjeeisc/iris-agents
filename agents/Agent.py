import json
import pandas as pd
import iris
from pydantic import BaseModel

from .utils import get_connection, create_class
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
        reasoning_effort: str | None = 'medium',
        persist_reasoning: bool = True,
        debug:bool = False
    ):
        self.debug = debug
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
                    response_format VARCHAR(4000),
                    reasoning_effort VARCHAR(50),
                    persist_reasoning INTEGER
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

        cur.execute(
            """
            SELECT
                agent_name,
                description,
                system_prompt_id,
                model,
                response_format,
                reasoning_effort,
                persist_reasoning
            FROM Agent
            WHERE agent_name = ?
            """,
            (name,),
        )
        row = cur.fetchone()

        fetch_only = all(value is Agent._UNSET for value in (description, system_prompt, model, toolkits, response_format))

        if fetch_only:
            if row is None:
                raise KeyError(f"No Agent found for '{name}'")

            _, description, system_prompt_id, model, response_format, reasoning_effort, persist_reasoning = row
            self.name = name
            self.description = description
            self.system_prompt = Prompt(system_prompt_id) if system_prompt_id else None
            self.model = model
            self.reasoning_effort = reasoning_effort
            self.persist_reasoning = bool(1 if persist_reasoning is None else persist_reasoning)
            self.response_format = (
                Message(response_format, None, message_type="Response")
                if response_format else None
            )

            cur.execute(
                "SELECT toolkit_id FROM AgentToolkit WHERE agent_name = ?",
                (self.name,),
            )
            toolkit_rows = cur.fetchall()
            self.toolkits = [Toolkit(toolkit_id) for (toolkit_id,) in toolkit_rows]
            return

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
                (agent_name, description, system_prompt_id, model, response_format, reasoning_effort, persist_reasoning)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    description_value,
                    system_prompt_id,
                    model,
                    response_format_name,
                    reasoning_effort,
                    int(bool(persist_reasoning)),
                ),
            )
            conn.commit()
        else:
            cur.execute(
                """UPDATE Agent SET
                    description = ?,
                    system_prompt_id = ?,
                    model = ?,
                    response_format = ?,
                    reasoning_effort = ?,
                    persist_reasoning = ?
                WHERE agent_name = ?""",
                (
                    description_value,
                    system_prompt_id,
                    model,
                    response_format_name,
                    reasoning_effort,
                    int(bool(persist_reasoning)),
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
        self.reasoning_effort = reasoning_effort
        self.persist_reasoning = bool(persist_reasoning)
        self.toolkits = []

        if toolkit_list:
            self.add_toolkits(toolkit_list)

    def __repr__(self) -> str:
        if not self.exists():
            raise KeyError(f"No Agent found for '{self.name}'")
        return (
            f"Agent(name={self.name!r}, model={self.model!r}, "
            f"system_prompt={getattr(self.system_prompt, 'name', None)!r})"
        )

    def create_gateway(self) -> None:
        cls_text = f'''Class Agents.Gateway.{self.name}Service Extends Ens.BusinessService
        {{
        Method OnProcessInput(pInput As Agents.Message.Request, pOutput As %RegisteredObject) As %Status
        {{
            Set sc = ..SendRequestSync("{self.name}", pInput, .pResponse)
            Set pOutput = pResponse
            Quit sc
        }}

        ClassMethod OnGetConnections(Output pArray As %String, pItem As Ens.Config.Item)
        {{
            Do ##super(.pArray, pItem)
            Set pArray("{self.name}") = ""
        }}
        }}
        '''
        create_class(f'Agents.Gateway.{self.name}Service', cls_text)

    def usage(self) -> dict:
        conn = get_connection()
        cur = conn.cursor()

        sql = """
            SELECT
                COALESCE(SUM(input_tokens), 0),
                COALESCE(SUM(output_tokens), 0),
                COALESCE(SUM(output_reasoning_tokens), 0),
                COALESCE(SUM(total_tokens), 0)
            FROM SQLUser.Usage
            WHERE agent_name = ?
        """

        cur.execute(sql, [self.name])
        row = cur.fetchone()

        return {
            "input_tokens": int(row[0] or 0),
            "output_tokens": int(row[1] or 0),
            "output_reasoning_tokens": int(row[2] or 0),
            "total_tokens": int(row[3] or 0),
        }

    def create_process(self) -> None:
        if self.response_format:
            default_response_cls = f"Agents.Message.{self.response_format.name}"
        else:
            default_response_cls = "Agents.Message.Response"

        system_prompt_text = self.system_prompt.text if self.system_prompt else ""
        system_prompt_json = json.dumps(system_prompt_text or "")
        persist_reasoning_int = 1 if self.persist_reasoning else 0

        toolkit_manifest_blocks = ""

        for toolkit in (self.toolkits or []):
            toolkit_name = toolkit.name.replace('"', '""')

            toolkit_manifest_blocks += f'''
                    Set toolkitName = "{toolkit_name}"
                    Set out = out_"Toolkit: "_toolkitName_$C(10)

                    Set cls = "Agents.Operation.Toolkit{toolkit_name}"
                    Set raw = ""
                    Set sc = $classmethod(cls, "ListTools", .raw)
                    If sc '= 1 {{
                        Set out = out_"  (tools/list failed)"_$C(10,10)
                    }} Else {{
                        Try {{
                            Set rawText = ##class(Agents.Utils.Common).ToJSONString(raw)
                            Set obj = ##class(%DynamicObject).%FromJSON(rawText)
                            Set result = obj.%Get("result")
                            Set tools = result.%Get("tools")

                            If tools="" ! tools.%Size()=0 {{
                                Set out = out_"  (no tools exposed)"_$C(10,10)
                            }} Else {{
                                For j=0:1:tools.%Size()-1 {{
                                    Set tool = tools.%Get(j)
                                    Set tname = tool.%Get("name")
                                    Set tdesc = tool.%Get("description")
                                    Set tschema = ""
                                    If tool.%IsDefined("inputSchema") {{
                                        Set tschema = tool.%Get("inputSchema").%ToJSON()
                                    }}

                                    Set out = out_"- "_tname
                                    If tdesc'="" Set out = out_": "_tdesc
                                    Set out = out_$C(10)
                                    If tschema'="" {{
                                        Set out = out_"  inputSchema: "_tschema_$C(10)
                                    }}
                                }}
                                Set out = out_$C(10)
                            }}
                        }} Catch ex {{
                            Set out = out_"  (failed to parse tools/list response)"_$C(10,10)
                        }}
                    }}
            '''

        cls_text = f'''Class Agents.Process.{self.name} Extends Ens.BusinessProcess
        {{
        Parameter SETTINGS = "";

        ClassMethod BuildToolManifest() As %String
        {{
            Set out = "You may use tools when needed."_$C(10,10)
            Set out = out_"When using a tool, return JSON with:"_$C(10)
            Set out = out_"- IsTool = true"_$C(10)
            Set out = out_"- Toolkit = toolkit name"_$C(10)
            Set out = out_"- Tool = tool name"_$C(10)
            Set out = out_"- Content = JSON string of tool arguments"_$C(10,10)
            Set out = out_"Available tools:"_$C(10,10)
            Set out = out_"Tool result handling rules:"_$C(10)
            Set out = out_"1. A prior developer message may contain a JSON object with keys toolkit, tool, and result. This is the authoritative output of a completed tool call."_$C(10)
            Set out = out_"2. The result field may contain a raw JSON-RPC response from the MCP server."_$C(10)
            Set out = out_"3. If result contains jsonrpc/result and no error, treat the tool call as successful and use the returned data to answer the user."_$C(10)
            Set out = out_"4. Do not call the same tool again with the same arguments if the prior tool result already answers the question."_$C(10)
            Set out = out_"5. Only call another tool if the previous result shows an error, is missing required information, or a different tool is needed."_$C(10)
            Set out = out_"6. If the previous tool result already contains enough information, return IsTool=false and produce the final answer JSON in Content."

            {toolkit_manifest_blocks}

            Set out = out_"Only call a tool when needed. If no tool is needed, return IsTool=false and put the final answer JSON in Content."
            Quit out
        }}

        ClassMethod GetSystemPrompt() As %String
        {{
            Quit {system_prompt_json}
        }}

        Method InvokeTool(
            pToolkit As %String,
            pTool As %String,
            pParams As %String,
            Output pResponse As Agents.Message.ToolResponse
        ) As %Status
        {{
            Set pResponse = ##class(Agents.Message.ToolResponse).%New()
            Set pResponse.Id = $SYSTEM.Util.CreateGUID()
            Set pResponse.Toolkit = pToolkit

            Set req = ##class(Agents.Message.ToolRequest).%New()
            Set req.Id = pResponse.Id
            Set req.Toolkit = pToolkit
            Set req.Name = pTool
            Set req.Params = ##class(Agents.Utils.Common).ToJSONString(pParams)

            Set sc = ..SendRequestSync(pToolkit, req, .toolResp)
            If $$$ISERR(sc) {{
                Set pResponse.Ok = 0
                Set pResponse.Result = $SYSTEM.Status.GetErrorText(sc)
                Quit sc
            }}

            Set pResponse = toolResp
            Quit $$$OK
        }}

        ClassMethod GetLLMTarget() As %String
        {{
            Set model = $ZCONVERT("{self.model}", "L")

            If $Extract(model, 1, 3) = "gpt" {{
                Quit "OpenAI"
            }}

            If $Extract(model, 1, 6) = "claude" {{
                Quit "Claude"
            }}

            Quit ""
        }}

        Method OnRequest(
            pRequest As %Library.Persistent,
            Output pResponse As %Library.Persistent
        ) As %Status
        {{
            Set sc = $$$OK
            Set stageSC = $$$OK
            Set logMsg = ""
            Set tLLMReq = ""
            Set tLLMResp = ""
            Set tToolResp = ""
            Set tFinalJSON = ""
            Set toolTurns = 0
            Set maxToolTurns = 3
            Set tUsageList = ##class(%DynamicArray).%New()
            Set tAssistantMessageId = ""
            Set tReasoningSummary = ""
            Set tReasoningSummarySep = ""
            Set tLatestResponseOutput = ""
            Set tLatestReasoningDetailed = ""
            Set tPersistReasoning = {persist_reasoning_int}

            Set tChatId = pRequest.ChatId
            Set tUserMessage = pRequest.Message
            Set tResponseType = pRequest.ResponseType
            If tResponseType="" {{
                Set tResponseType = "{default_response_cls}"
            }}
            Set tReasoningEffort = pRequest.ReasoningEffort
            If tReasoningEffort="" {{
                Set tReasoningEffort = "medium"
            }}

            {'Set logMsg = "OnRequest start agent="_..%ConfigName_" chatId="_$Get(tChatId)  $$$LOGINFO(logMsg)' if self.debug else ''}
            {'Set logMsg = "User message="_$Extract($Get(tUserMessage),1,300) $$$LOGINFO(logMsg)' if self.debug else ''}

            //
            // Persist the user turn first so BuildChatJSON(chatId, ...) includes it.
            //
            If tChatId'="" {{
                Set stageSC = $$$OK
                Try {{
                    Set sc = ##class(Agents.Utils.Common).AppendChat(
                        tChatId,
                        "user",
                        tUserMessage
                    )
                    {'$$$LOGSTATUS(sc)' if self.debug else ''}
                    If $$$ISERR(sc) {{
                        Set stageSC = sc
                    }}
                }} Catch ex {{
                    $$$LOGERROR("Stage=AppendUserMessage exception")
                    Set stageSC = ex.AsStatus()
                }}
                If $$$ISERR(stageSC) {{
                    Quit stageSC
                }}
            }}

            Set stageSC = $$$OK
            Try {{
                Set tLLMReq = ##class(Agents.Message.LLMRequest).%New()
                Set tLLMReq.Model = "{self.model}"
                Set tLLMReq.ResponseType = tResponseType
                Set tLLMReq.ReasoningEffort = tReasoningEffort
                Set tLLMReq.ReasoningDetailed = ""

                If tChatId'="" {{
                    Set tLLMReq.ReasoningDetailed = ##class(Agents.Utils.Common).GetLatestReasoningDetailed(tChatId)
                    Set tLLMReq.Chat = ##class(Agents.Utils.Common).ToJSONString(
                        ##class(Agents.Utils.Production).BuildChatJSON(
                            tChatId,
                            "",
                            ..GetSystemPrompt(),
                            ..BuildToolManifest()
                        )
                    )
                }} Else {{
                    Set tLLMReq.Chat = ##class(Agents.Utils.Common).ToJSONString(
                        ##class(Agents.Utils.Production).BuildChatJSON(
                            "",
                            tUserMessage,
                            ..GetSystemPrompt(),
                            ..BuildToolManifest()
                        )
                    )
                }}
                {'Set logMsg = "Stage=BuildFirstLLMRequest chatSample="_$Extract(tLLMReq.Chat,1,1500) $$$LOGINFO(logMsg)' if self.debug else ''}
            }} Catch ex {{
                $$$LOGERROR("Stage=BuildFirstLLMRequest exception")
                Set stageSC = ex.AsStatus()
            }}
            If $$$ISERR(stageSC) {{
                Quit stageSC
            }}

            Set stageSC = $$$OK
            Try {{
                Set sc = ..SendRequestSync(..GetLLMTarget(), tLLMReq, .tLLMResp)
                {'$$$LOGSTATUS(sc)' if self.debug else ''}
                If $$$ISERR(sc) {{
                    Set stageSC = sc
                }}
            }} Catch ex {{
                $$$LOGERROR("Stage=FirstLLMCall exception")
                Set stageSC = ex.AsStatus()
            }}
            If $$$ISERR(stageSC) {{
                Quit stageSC
            }}

            Set stageSC = $$$OK
            Try {{
                If $IsObject(tLLMResp) {{
                    If tLLMResp.Usage'="" {{
                        Do tUsageList.%Push(tLLMResp.Usage)
                    }}
                }}
                {'$$$LOGSTATUS(sc)' if self.debug else ''}
                If $$$ISERR(sc) {{
                    Set stageSC = sc
                }}
            }} Catch ex {{
                $$$LOGERROR("Stage=LogFirstLLMUsage exception")
                Set stageSC = ex.AsStatus()
            }}
            If $$$ISERR(stageSC) {{
                Quit stageSC
            }}

            If '$IsObject(tLLMResp) {{
                Quit $$$ERROR($$$GeneralError,"LLM returned no object")
            }}

            If tLLMResp.ReasoningSummary'="" {{
                Set tReasoningSummary = tReasoningSummary _ tReasoningSummarySep _ tLLMResp.ReasoningSummary
                Set tReasoningSummarySep = $C(10,10)
            }}
            If tLLMResp.ResponseOutput'="" {{
                Set tLatestResponseOutput = tLLMResp.ResponseOutput
            }}
            If tLLMResp.ReasoningDetailed'="" {{
                Set tLatestReasoningDetailed = tLLMResp.ReasoningDetailed
            }}

            Set toolTurns = 0
            Set maxToolTurns = 3
            Set chainExceeded = 0
            Set stopLoop = 0

            While tLLMResp.IsTool=1 {{
                Set toolTurns = toolTurns + 1
                If toolTurns>maxToolTurns {{
                    Set chainExceeded = 1
                    Set stageSC = $$$ERROR($$$GeneralError,"Tool chain exceeded max depth")
                    Quit
                }}

                {'Set logMsg = "ToolTurn="_toolTurns_" Toolkit="_tLLMResp.Toolkit_" Tool="_tLLMResp.Tool $$$LOGINFO(logMsg)' if self.debug else ''}
                {'Set logMsg = "Tool params="_$Extract(##class(Agents.Utils.Common).ToJSONString(tLLMResp.Content),1,1000) $$$LOGINFO(logMsg)' if self.debug else ''}

                Set stageSC = $$$OK
                Try {{
                    Set sc = ..InvokeTool(tLLMResp.Toolkit, tLLMResp.Tool, tLLMResp.Content, .tToolResp)
                    {'$$$LOGSTATUS(sc)' if self.debug else ''}
                    If $$$ISERR(sc) {{
                        Set stageSC = sc
                    }}
                }} Catch ex {{
                    $$$LOGERROR("Stage=InvokeTool exception")
                    Set stageSC = ex.AsStatus()
                }}
                If $$$ISERR(stageSC) {{
                    Set stopLoop = 1
                    Quit
                }}

                {'Set logMsg = "Tool result="_$Extract(##class(Agents.Utils.Common).ToJSONString(tToolResp.Result),1,1500) $$$LOGINFO(logMsg)' if self.debug else ''}

                Set stageSC = $$$OK
                Try {{
                    Set sc = ##class(Agents.Utils.Common).LogToolUsage(
                        tChatId,
                        ..%ConfigName,
                        tLLMResp.Toolkit,
                        tLLMResp.Tool,
                        ##class(Agents.Utils.Common).ToJSONString(tLLMResp.Content),
                        +tToolResp.OkGet(),
                        ##class(Agents.Utils.Common).ToJSONString(tToolResp.ResultGet())
                    )
                    {'$$$LOGSTATUS(sc)' if self.debug else ''}
                    If $$$ISERR(sc) {{
                        Set stageSC = sc
                    }}
                }} Catch ex {{
                    $$$LOGERROR("Stage=LogToolUsage exception")
                    Set stageSC = ex.AsStatus()
                }}
                If $$$ISERR(stageSC) {{
                    Set stopLoop = 1
                    Quit
                }}

                Set stageSC = $$$OK
                Try {{
                    Set tLLMReq = ##class(Agents.Message.LLMRequest).%New()
                    Set tLLMReq.Model = "{self.model}"
                    Set tLLMReq.ResponseType = tResponseType
                    Set tLLMReq.ReasoningEffort = tReasoningEffort
                    Set tLLMReq.ReasoningDetailed = tLatestReasoningDetailed

                    Set tLLMReq.Chat = ##class(Agents.Utils.Common).ToJSONString(
                        ##class(Agents.Utils.Production).BuildNextLLMChatJSON(
                            tChatId,
                            tUserMessage,
                            tLLMResp.Toolkit,
                            tLLMResp.Tool,
                            tToolResp.Result,
                            ..GetSystemPrompt(),
                            ..BuildToolManifest(),
                            tLatestResponseOutput
                        )
                    )

                    {'Set logMsg = "Stage=BuildNextLLMRequest chatSample="_$Extract(tLLMReq.Chat,1,1500) $$$LOGINFO(logMsg)' if self.debug else ''}
                }} Catch ex {{
                    $$$LOGERROR("Stage=BuildNextLLMRequest exception")
                    Set stageSC = ex.AsStatus()
                }}
                If $$$ISERR(stageSC) {{
                    Set stopLoop = 1
                    Quit
                }}

                Set stageSC = $$$OK
                Try {{
                    Set tLLMResp = ""
                    Set sc = ..SendRequestSync(..GetLLMTarget(), tLLMReq, .tLLMResp)
                    {'$$$LOGSTATUS(sc)' if self.debug else ''}
                    If $$$ISERR(sc) {{
                        Set stageSC = sc
                    }}
                }} Catch ex {{
                    $$$LOGERROR("Stage=NextLLMCall exception")
                    Set stageSC = ex.AsStatus()
                }}
                If $$$ISERR(stageSC) {{
                    Set stopLoop = 1
                    Quit
                }}

                Set stageSC = $$$OK
                Try {{
                    If $IsObject(tLLMResp) {{
                        If tLLMResp.Usage'="" {{
                            Do tUsageList.%Push(tLLMResp.Usage)
                        }}
                    }}
                    {'$$$LOGSTATUS(sc)' if self.debug else ''}
                    If $$$ISERR(sc) {{
                        Set stageSC = sc
                    }}
                }} Catch ex {{
                    $$$LOGERROR("Stage=LogNextLLMUsage exception")
                    Set stageSC = ex.AsStatus()
                }}
                If $$$ISERR(stageSC) {{
                    Set stopLoop = 1
                    Quit
                }}

                If '$IsObject(tLLMResp) {{
                    Set stageSC = $$$ERROR($$$GeneralError,"LLM returned no object during tool chain")
                    Set stopLoop = 1
                    Quit
                }}

                If tLLMResp.ReasoningSummary'="" {{
                    Set tReasoningSummary = tReasoningSummary _ tReasoningSummarySep _ tLLMResp.ReasoningSummary
                    Set tReasoningSummarySep = $C(10,10)
                }}

                If tLLMResp.ResponseOutput'="" {{
                    Set tLatestResponseOutput = tLLMResp.ResponseOutput
                }}
                If tLLMResp.ReasoningDetailed'="" {{
                    Set tLatestReasoningDetailed = tLLMResp.ReasoningDetailed
                }}
            }}

            If chainExceeded=1 {{
                Quit stageSC
            }}
            If stopLoop=1 {{
                Quit stageSC
            }}
            If $$$ISERR(stageSC) {{
                Quit stageSC
            }}

            Set tFinalJSON = ##class(Agents.Utils.Common).ToJSONString(tLLMResp.Content)
            {'Set logMsg = "Final JSON="_$Extract(tFinalJSON,1,1500) $$$LOGINFO(logMsg)' if self.debug else ''}

            Set stageSC = $$$OK
            Try {{
                Set sc = ##class(Agents.Utils.Common).ImportJSONToResponse(tFinalJSON, tResponseType, .pResponse)
                {'$$$LOGSTATUS(sc)' if self.debug else ''}
                If $$$ISERR(sc) {{
                    Set stageSC = sc
                }}
            }} Catch ex {{
                $$$LOGERROR("Stage=ImportJSONToResponse exception")
                Set stageSC = ex.AsStatus()
            }}
            If $$$ISERR(stageSC) {{
                Quit stageSC
            }}

            If tChatId'="" {{
                Set stageSC = $$$OK
                Try {{
                    Set tStoredReasoningSummary = ""
                    Set tStoredReasoningDetailed = ""

                    If tPersistReasoning=1 {{
                        Set tStoredReasoningSummary = tReasoningSummary
                        Set tStoredReasoningDetailed = tLatestReasoningDetailed
                    }}

                    Set sc = ##class(Agents.Utils.Common).AppendChatReturnMessageId(
                        tChatId,
                        "assistant",
                        tFinalJSON,
                        tStoredReasoningSummary,
                        tStoredReasoningDetailed,
                        .tAssistantMessageId
                    )
                    {'$$$LOGSTATUS(sc)' if self.debug else ''}
                    If $$$ISERR(sc) {{
                        Set stageSC = sc
                    }}
                }} Catch ex {{
                    $$$LOGERROR("Stage=AppendFinalAnswer exception")
                    Set stageSC = ex.AsStatus()
                }}
                If $$$ISERR(stageSC) {{
                    Quit stageSC
                }}
            }}

            For i=0:1:tUsageList.%Size()-1 {{
                Set oneUsage = tUsageList.%Get(i)
                Set stageSC = $$$OK
                Try {{
                    Set sc = ##class(Agents.Utils.Common).LogLLMUsage(
                        tChatId,
                        tAssistantMessageId,
                        "{self.name}",
                        ##class(Agents.Utils.Common).GetRunningProductionName(),
                        "{self.model}",
                        tReasoningEffort,
                        oneUsage
                    )
                    {'$$$LOGSTATUS(sc)' if self.debug else ''}
                    If $$$ISERR(sc) {{
                        Set stageSC = sc
                    }}
                }} Catch ex {{
                    $$$LOGERROR("Stage=LogBufferedLLMUsage exception")
                    Set stageSC = ex.AsStatus()
                }}
                If $$$ISERR(stageSC) {{
                    Quit stageSC
                }}
            }}

            Quit $$$OK
        }}

        Method OnResponse(
            pRequest As %Library.Persistent,
            ByRef pResponse As %Library.Persistent,
            pCallRequest As %Library.Persistent,
            pCallResponse As %Library.Persistent,
            pCompletionKey As %String
        ) As %Status
        {{
            Quit $$$OK
        }}

        }}
        '''
        create_class(f'Agents.Process.{self.name}', cls_text)

    def ensure_runtime_classes(self) -> None:
        self.create_gateway()
        self.create_process()

    def add_toolkits(self, toolkits: list[Toolkit]) -> None:
        if not self.exists():
            return 'Agent does not exist'
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
        if not self.exists():
            raise KeyError(f"No Agent found for '{self.name}'")
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
        reasoning_effort: str | None = None,
    ) -> str:
        
        if not self.exists():
            raise KeyError(f"No Agent found for '{self.name}'")
        
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
            chat_id = ''
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
            "reasoningEffort": reasoning_effort or self.reasoning_effort,
        }

        request_object = irispy.classMethodObject("Agents.Message.Request", "%New")
        request_object.invoke("%JSONImport", json.dumps(payload))

        gateway_item = f'{self.name}Gateway'

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

        if not self.exists():
            raise KeyError(f"No Agent found for '{self.name}'")
        
        if not isinstance(other, Agent):
            return NotImplemented
        return self.name == other.name
    
    def delete(self) -> None:
        """
        Delete only agent-owned artifacts.

        This removes:
        - Agent row from SQLUser.Agent
        - AgentToolkit rows for this agent from SQLUser.AgentToolkit
        - Gateway class: Agents.Gateway.<AgentName>Service
        - Process class: Agents.Process.<AgentName>

        This does NOT remove:
        - shared message classes
        - toolkit definitions in SQLUser.Toolkit
        - toolkit operation classes
        - shared utility classes
        - productions
        """

        if not self.exists():
            raise KeyError(f"No Agent found for '{self.name}'")
        
        conn = get_connection()
        cur = conn.cursor()
        irispy = get_connection(True)

        errors = []

        # Delete DB rows first
        try:
            cur.execute("DELETE FROM AgentToolkit WHERE agent_name = ?", (self.name,))
            conn.commit()
        except Exception as e:
            errors.append(f"DELETE AgentToolkit failed for {self.name}: {e}")

        try:
            cur.execute("DELETE FROM Agent WHERE agent_name = ?", (self.name,))
            conn.commit()
        except Exception as e:
            errors.append(f"DELETE Agent failed for {self.name}: {e}")

        # Delete runtime classes
        gateway_class = f"Agents.Gateway.{self.name}Service"
        process_class = f"Agents.Process.{self.name}"

        try:
            sc = irispy.classMethodValue(
                "Agents.Admin",
                "DeleteClassIfExists",
                gateway_class
            )
            if sc != 1:
                errors.append(
                    f"DeleteClassIfExists({gateway_class}) failed: "
                    f"{irispy.classMethodValue('%SYSTEM.Status', 'GetErrorText', sc)}"
                )
        except Exception as e:
            errors.append(f"DeleteClassIfExists({gateway_class}) raised: {e}")

        try:
            sc = irispy.classMethodValue(
                "Agents.Admin",
                "DeleteClassIfExists",
                process_class
            )
            if sc != 1:
                errors.append(
                    f"DeleteClassIfExists({process_class}) failed: "
                    f"{irispy.classMethodValue('%SYSTEM.Status', 'GetErrorText', sc)}"
                )
        except Exception as e:
            errors.append(f"DeleteClassIfExists({process_class}) raised: {e}")

        if errors:
            raise RuntimeError("Agent cleanup encountered errors:\n" + "\n".join(errors))
        
    def exists(self) -> bool:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM Agent WHERE agent_name = ?", (self.name,))
        return cur.fetchone() is not None
