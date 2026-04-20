from .utils import create_class, ensure_agents_namespace, ensure_common_utils, ensure_schema, get_connection
from .models import ToolRequest, ToolResponse
from .Message import Message
import time
from urllib.parse import urlparse


class Toolkit:

    def __init__(self, name: str, url: str | None = None):
        self.name = name
        if url is not None:
            ensure_agents_namespace()

        ensure_schema('Toolkit')
        should_rebuild_runtime = False

        conn = get_connection()
        cur = conn.cursor()

        # Fetch existing
        cur.execute(
            'SELECT toolkit_url FROM Agents.Toolkit WHERE toolkit_id = ?',
            (name,)
        )
        row = cur.fetchone()

        # Fetch-only mode
        if url is None:
            if not row:
                raise KeyError(f"No Toolkit found for '{name}'")
            self.url = row[0]
            return

        # Create/update mode
        if row:
            existing_url = row[0]
            if existing_url != url:
                cur.execute(
                    'UPDATE Agents.Toolkit SET toolkit_url = ? WHERE toolkit_id = ?',
                    (url, name)
                )
                conn.commit()
                should_rebuild_runtime = True
            self.url = url
        else:
            cur.execute(
                'INSERT INTO Agents.Toolkit (toolkit_id, toolkit_url) VALUES (?, ?)',
                (name, url)
            )
            conn.commit()
            self.url = url
            should_rebuild_runtime = True

        self.ensure_runtime_classes(force=should_rebuild_runtime)

    def ensure_runtime_classes(self, force: bool = False) -> None:
        irispy = get_connection(True)

        if int(irispy.classMethodValue('%Dictionary.ClassDefinition', '%ExistsId', 'Agents.Message.ToolRequest')) != 1:
            Message('ToolRequest', ToolRequest, message_type='Request')
        if int(irispy.classMethodValue('%Dictionary.ClassDefinition', '%ExistsId', 'Agents.Message.ToolResponse')) != 1:
            Message('ToolResponse', ToolResponse, message_type='Response')

        deps = ['Agents.Message.ToolRequest', 'Agents.Message.ToolResponse']
        deadline = time.time() + 10.0
        missing = deps[:]

        while time.time() < deadline:
            missing = [
                dep for dep in deps
                if int(irispy.classMethodValue('%Dictionary.ClassDefinition', '%ExistsId', dep)) != 1
            ]
            if not missing:
                break
            time.sleep(0.25)

        if missing:
            raise RuntimeError(
                'Dependency classes still missing after 10 seconds: '
                + ", ".join(missing)
            )

        cls_name = f'Agents.Operation.Toolkit{self.name}'
        if int(irispy.classMethodValue('%Dictionary.ClassDefinition', '%ExistsId', 'Agents.Utils.Common')) != 1:
            ensure_common_utils()
        if not force and int(irispy.classMethodValue('%Dictionary.ClassDefinition', '%ExistsId', cls_name)) == 1:
            return

        parsed = urlparse(self.url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        path = parsed.path or '/mcp'
        https = 1 if parsed.scheme == 'https' else 0

        cls_text = f'''Class {cls_name} Extends Ens.BusinessOperation
        {{
        Parameter INVOCATION = "Queue";

        XData MessageMap
        {{
        <MapItem MessageType="Agents.Message.ToolRequest">
        <Method>InvokeTool</Method>
        </MapItem>
        }}

        ClassMethod SessionCredentialName() As %String
        {{
            Quit "MCP_SESSION_{self.name}"
        }}

        ClassMethod SaveSessionId(pSessionId As %String) As %Status
        {{
            Quit ##class(Ens.Config.Credentials).SetCredential(
                ..SessionCredentialName(),
                "MCP",
                pSessionId,
                1
            )
        }}

        ClassMethod GetSessionId(Output pSessionId As %String) As %Status
        {{
            Set pSessionId = ##class(Ens.Config.Credentials).GetValue(..SessionCredentialName(), "Password")
            Set pSessionId = $ZSTRIP($Get(pSessionId), "<>W")
            Set pSessionId = $TR(pSessionId, $CHAR(13,10), "")
            Quit $$$OK
        }}

        ClassMethod InitializeSession(Output pSessionId As %String) As %Status
        {{
            Set pSessionId = ""

            Set http = ##class(%Net.HttpRequest).%New()
            Set http.Https = {https}
            Set http.Server = "{host}"
            Set http.Port = {port}
            Set http.Timeout = 30
            Do http.SetHeader("Content-Type","application/json")
            Do http.SetHeader("Accept","application/json")

            Set params = ##class(%DynamicObject).%New()
            Do params.%Set("protocolVersion", "2024-11-05")
            Do params.%Set("capabilities", ##class(%DynamicObject).%New())

            Set clientInfo = ##class(%DynamicObject).%New()
            Do clientInfo.%Set("name", "iris-agent")
            Do clientInfo.%Set("version", "1.0")
            Do params.%Set("clientInfo", clientInfo)

            Set init = ##class(%DynamicObject).%New()
            Do init.%Set("jsonrpc","2.0")
            Do init.%Set("id",1)
            Do init.%Set("method","initialize")
            Do init.%Set("params", params)

            Set http.EntityBody = ##class(%Stream.GlobalCharacter).%New()
            Do http.EntityBody.Write(init.%ToJSON())
            Do http.EntityBody.Rewind()

            Set sc = http.Post("{path}")
            If $$$ISERR(sc) Quit sc

            If '$IsObject(http.HttpResponse) {{
                Quit $$$ERROR($$$GeneralError, "No HTTP response from MCP initialize")
            }}

            Set pSessionId = http.HttpResponse.GetHeader("MCP-Session-Id")
            Set pSessionId = $ZSTRIP($Get(pSessionId), "<>W")
            Set pSessionId = $TR(pSessionId, $CHAR(13,10), "")

            If pSessionId="" {{
                Quit $$$ERROR($$$GeneralError, "Toolkit initialize did not return MCP-Session-Id")
            }}

            Quit ..SaveSessionId(pSessionId)
        }}

        ClassMethod EnsureSession(Output pSessionId As %String) As %Status
        {{
            Set sc = ..GetSessionId(.pSessionId)
            If $$$ISERR(sc) Quit sc

            If pSessionId'="" Quit $$$OK

            Quit ..InitializeSession(.pSessionId)
        }}

        ClassMethod PostRaw(
            pSessionId As %String,
            pPayload As %String,
            Output pBody As %String,
            Output pHttpStatus As %Integer = 0
        ) As %Status
        {{
            Set pBody = ""
            Set pHttpStatus = 0

            Set http = ##class(%Net.HttpRequest).%New()
            Set http.Https = {https}
            Set http.Server = "{host}"
            Set http.Port = {port}
            Set http.Timeout = 60
            Do http.SetHeader("Content-Type","application/json")
            Do http.SetHeader("Accept","application/json")
            If $Get(pSessionId)'="" {{
                Do http.SetHeader("MCP-Session-Id", pSessionId)
            }}

            Set http.EntityBody = ##class(%Stream.GlobalCharacter).%New()
            Do http.EntityBody.Write(pPayload)
            Do http.EntityBody.Rewind()

            Set sc = http.Post("{path}")
            If $$$ISERR(sc) Quit sc

            If $IsObject(http.HttpResponse) {{
                Set pHttpStatus = +http.HttpResponse.StatusCode
                Set pBody = ##class(Agents.Utils.Common).ToText(http.HttpResponse.Data)
            }}

            Quit $$$OK
        }}

        ClassMethod PostWithSession(
            pMethod As %String,
            pParams As %DynamicObject,
            Output pBody As %String
        ) As %Status
        {{
            Set pBody = ""

            Set sc = ..EnsureSession(.sessionId)
            If $$$ISERR(sc) Quit sc

            Set call = ##class(%DynamicObject).%New()
            Do call.%Set("jsonrpc","2.0")
            Do call.%Set("id",1)
            Do call.%Set("method", pMethod)
            Do call.%Set("params", $Select($IsObject(pParams):pParams, 1:##class(%DynamicObject).%New()))

            Set sc = ..PostRaw(sessionId, call.%ToJSON(), .pBody, .httpStatus)
            If $$$ISERR(sc) Quit sc

            If (httpStatus=401) ! (pBody["invalid session") ! (pBody["MCP-Session-Id") {{
                Set sc = ..InitializeSession(.sessionId)
                If $$$ISERR(sc) Quit sc
                Set sc = ..PostRaw(sessionId, call.%ToJSON(), .pBody, .httpStatus)
                If $$$ISERR(sc) Quit sc
            }}

            Quit $$$OK
        }}

        ClassMethod ListTools(Output pResult As %String) As %Status
        {{
            Set params = ##class(%DynamicObject).%New()
            Quit ..PostWithSession("tools/list", params, .pResult)
        }}

        ClassMethod CallTool(
            pTool As %String,
            pParamsJSON As %String = "",
            Output pOk As %Integer = 0,
            Output pResult As %String = ""
        ) As %Status
        {{
            Set sc = $$$OK
            Set body = ""
            Set pOk = 0
            Set pResult = ""
            Set parseOK = 1

            Try {{
                Set paramsText = $Get(pParamsJSON)
                If paramsText="" {{
                    Set paramsText = "{{}}"
                }}

                Set params = ##class(%DynamicObject).%FromJSON(paramsText)
                If '$IsObject(params) {{
                    Set parseOK = 0
                    Set pResult = "Tool params must be a JSON object."
                }} ElseIf 'params.%IsA("%Library.DynamicObject") {{
                    Set parseOK = 0
                    Set pResult = "Tool params must be a JSON object."
                }}
            }} Catch ex {{
                Set parseOK = 0
                Set pResult = "Invalid tool params JSON: "_ex.DisplayString()
            }}

            If parseOK {{
                Set callParams = ##class(%DynamicObject).%New()
                Do callParams.%Set("name", pTool)
                Do callParams.%Set("arguments", params)

                Set sc = ..PostWithSession("tools/call", callParams, .body)
                If $$$ISERR(sc) {{
                    Set pResult = $SYSTEM.Status.GetErrorText(sc)
                    Set sc = $$$OK
                }} Else {{
                    Set pOk = 1
                    Set pResult = ##class(Agents.Utils.Common).ToJSONString(body)
                }}
            }}

            Quit sc
        }}

        Method InvokeTool(pRequest As Agents.Message.ToolRequest, Output pResponse As Agents.Message.ToolResponse) As %Status
        {{
            Set sc = $$$OK
            Set paramsText = ""
            Set ok = 0
            Set result = ""

            Set pResponse = ##class(Agents.Message.ToolResponse).%New()
            Set pResponse.Id = pRequest.Id
            Set pResponse.Toolkit = pRequest.Toolkit
            Set pResponse.Ok = 0
            Set pResponse.Result = ""

            Set paramsText = ##class(Agents.Utils.Common).ToJSONString(pRequest.Params)
            Set sc = ..CallTool(pRequest.Name, paramsText, .ok, .result)
            Set pResponse.Ok = ok
            Set pResponse.Result = result

            Quit sc
        }}
        }}
        '''

        create_class(cls_name, cls_text)
