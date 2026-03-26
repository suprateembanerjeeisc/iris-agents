from .utils import get_connection ,create_class, ensure_common_utils
import iris

class Toolkit:

    def __init__(self, name: str, url: str | None = None):

        ensure_common_utils()

        conn = get_connection()
        cur = conn.cursor()

        # Ensure table exists
        cur.execute("""
        SELECT TABLE_NAME 
        FROM INFORMATION_SCHEMA.Tables 
        WHERE TABLE_SCHEMA = 'SQLUser'
        """)
        tables = [r[0] for r in cur.fetchall()]

        if 'Toolkit' not in tables:
            cur.execute("""
            CREATE TABLE Toolkit (
                toolkit_id VARCHAR(200) NOT NULL PRIMARY KEY,
                toolkit_url VARCHAR(1000) NOT NULL
            )
            """)
            conn.commit()

        # Fetch existing
        cur.execute(
            "SELECT toolkit_url FROM Toolkit WHERE toolkit_id = ?",
            (name,)
        )
        row = cur.fetchone()

        # CASE 1: Toolkit exists
        if row:
            existing_url = row[0]

            if url and existing_url != url:
                # Update if URL changed
                cur.execute(
                    "UPDATE Toolkit SET toolkit_url = ? WHERE toolkit_id = ?",
                    (url, name)
                )
                conn.commit()
                self.url = url
            else:
                self.url = existing_url

        # CASE 2: Toolkit does not exist
        else:
            if not url:
                raise KeyError(f"No Toolkit found for '{name}', and no 'url' was provided.")

            cur.execute(
                "INSERT INTO Toolkit (toolkit_id, toolkit_url) VALUES (?, ?)",
                (name, url)
            )
            conn.commit()
            self.url = url

        self.name = name

        cls_name = f'Agents.Operation.Toolkit{self.name}'

        # parse host/port/path
        from urllib.parse import urlparse
        parsed = urlparse(self.url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/mcp"
        https = 1 if parsed.scheme == "https" else 0

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

            // retry once if session is stale
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

        Method InvokeTool(pRequest As Agents.Message.ToolRequest, Output pResponse As Agents.Message.ToolResponse) As %Status
        {{
            Set sc = $$$OK
            Set body = ""
            Set params = ""
            Set paramsText = ""
            Set parseOK = 1

            Set pResponse = ##class(Agents.Message.ToolResponse).%New()
            Set pResponse.Id = pRequest.Id
            Set pResponse.Toolkit = pRequest.Toolkit
            Set pResponse.Ok = 0
            Set pResponse.Result = ""

            Try {{
                Set paramsText = ##class(Agents.Utils.Common).ToJSONString(pRequest.Params)
                Set params = ##class(%DynamicObject).%FromJSON(paramsText)
            }} Catch ex {{
                Set parseOK = 0
                Set pResponse.Result = "Invalid tool params JSON: "_ex.DisplayString()
            }}

            If parseOK {{
                Set callParams = ##class(%DynamicObject).%New()
                Do callParams.%Set("name", pRequest.Name)
                Do callParams.%Set("arguments", params)

                Set sc = ..PostWithSession("tools/call", callParams, .body)
                If $$$ISERR(sc) {{
                    Set pResponse.Result = $SYSTEM.Status.GetErrorText(sc)
                    Set sc = $$$OK
                }} Else {{
                    Set pResponse.Ok = 1
                    Set pResponse.Result = ##class(Agents.Utils.Common).ToJSONString(body)
                }}
            }}

            Quit sc
        }}
        }}
        '''

        create_class(cls_name, cls_text)