from dotenv import load_dotenv
import iris
import json
import os

from .Toolkit import Toolkit
from .Message import Message
from .Agent import Agent
from .models import LLMRequest, LLMResponse, Request, Response, LLMOutput
from .utils import get_connection, create_class, ensure_common_utils, ensure_production_utils

load_dotenv()

class Production:

    def __init__(self, 
             name: str,
             agents: list[Agent] | None = None,
             openai_api_key: str | None = None):
        self.name = name
        self.openai_api_key = openai_api_key
        irispy = get_connection(True)

        if agents is None and openai_api_key is None:
            if not irispy.classMethodObject('Ens.Config.Production', '%OpenId', f'User.{self.name}'):
                raise RuntimeError(f'Production class not found: User.{self.name}')

            elements = irispy.classMethodObject('%ResultSet', '%New', 'Ens.Config.Production:EnumerateConfigItemNames')
            elements.invoke('Execute', f'User.{self.name}', '')
            self.agents = []

            while elements.invoke('%Next'):
                clsname = elements.invoke('GetData', 3)
                if isinstance(clsname, str) and clsname.startswith('Agents.Process.'):
                    agent_name = clsname.split('Agents.Process.', 1)[1].strip()
                    if agent_name:
                        self.agents.append(Agent(agent_name))
        else:
            self.agents = agents or []

            if not self.agents:
                existing = irispy.classMethodObject('Ens.Config.Production', '%OpenId', f'User.{self.name}')
                if existing:
                    elements = irispy.classMethodObject('%ResultSet', '%New', 'Ens.Config.Production:EnumerateConfigItemNames')
                    elements.invoke('Execute', f'User.{self.name}', '')
                    while elements.invoke('%Next'):
                        clsname = elements.invoke('GetData', 3)
                        if isinstance(clsname, str) and clsname.startswith('Agents.Process.'):
                            agent_name = clsname.split('Agents.Process.', 1)[1].strip()
                            if agent_name:
                                self.agents.append(Agent(agent_name))

            self.build()

    def ensure_tool_usage_table(self):
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.Tables
            WHERE TABLE_TYPE='BASE TABLE'
            AND TABLE_SCHEMA='SQLUser'
            AND TABLE_NAME='ToolUsage'
        """)
        row = cur.fetchone()

        if not row:
            cur.execute("""
                CREATE TABLE ToolUsage (
                    usage_id BIGINT IDENTITY PRIMARY KEY,
                    usage_ts TIMESTAMP NOT NULL,
                    chat_id VARCHAR(200),
                    agent_name VARCHAR(200) NOT NULL,
                    toolkit VARCHAR(200) NOT NULL,
                    tool_name VARCHAR(200) NOT NULL,
                    request_payload VARCHAR(50000) NOT NULL,
                    response_ok INTEGER NOT NULL,
                    response_payload VARCHAR(50000) NOT NULL
                )
            """)
            conn.commit()

        try:
            cur.execute("CREATE INDEX idx_toolusage_chat_ts ON ToolUsage (chat_id, usage_ts)")
            conn.commit()
        except Exception:
            pass

    def ensure_llm_usage_table(self):
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.Tables
            WHERE TABLE_TYPE='BASE TABLE'
            AND TABLE_SCHEMA='SQLUser'
            AND TABLE_NAME='Usage'
        """)
        row = cur.fetchone()

        if not row:
            cur.execute("""
                CREATE TABLE Usage (
                    usage_id BIGINT IDENTITY PRIMARY KEY,
                    usage_ts TIMESTAMP NOT NULL,
                    chat_id VARCHAR(200),
                    message_id BIGINT,
                    agent_name VARCHAR(200),
                    production_name VARCHAR(200),
                    model VARCHAR(200),
                    reasoning_effort VARCHAR(50),
                    input_tokens BIGINT,
                    output_tokens BIGINT,
                    total_tokens BIGINT,
                    input_cached_tokens BIGINT,
                    input_audio_tokens BIGINT,
                    output_audio_tokens BIGINT,
                    output_reasoning_tokens BIGINT,
                    duration_ms BIGINT
                )
            """)
            conn.commit()
        else:
            cur.execute("""
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA='SQLUser'
                AND TABLE_NAME='Usage'
                AND COLUMN_NAME='message_id'
            """)
            if not cur.fetchone():
                cur.execute("""
                    ALTER TABLE SQLUser.Usage
                    ADD message_id BIGINT
                """)
                conn.commit()

            cur.execute("""
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA='SQLUser'
                AND TABLE_NAME='Usage'
                AND COLUMN_NAME='duration_ms'
            """)
            if not cur.fetchone():
                cur.execute("""
                    ALTER TABLE SQLUser.Usage
                    ADD duration_ms BIGINT
                """)
                conn.commit()

        try:
            cur.execute("CREATE INDEX idx_usage_chat_ts ON Usage (chat_id, usage_ts)")
            conn.commit()
        except Exception:
            pass

        try:
            cur.execute("CREATE INDEX idx_usage_agent_ts ON Usage (agent_name, usage_ts)")
            conn.commit()
        except Exception:
            pass

        try:
            cur.execute("CREATE INDEX idx_usage_prod_ts ON Usage (production_name, usage_ts)")
            conn.commit()
        except Exception:
            pass

        try:
            cur.execute("CREATE INDEX idx_usage_model_ts ON Usage (model, usage_ts)")
            conn.commit()
        except Exception:
            pass

    def usage(
        self,
        agents: list[Agent] | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> dict:
        conn = get_connection()
        cur = conn.cursor()

        sql = """
            SELECT
                COALESCE(SUM(input_tokens), 0),
                COALESCE(SUM(output_tokens), 0),
                COALESCE(SUM(output_reasoning_tokens), 0),
                COALESCE(SUM(total_tokens), 0)
            FROM SQLUser.Usage
            WHERE production_name = ?
        """
        params = [self.name]

        agent_names = None
        if agents is not None:
            if not isinstance(agents, list):
                raise TypeError("agents must be list[Agent] | None")
            if any(not isinstance(agent, Agent) for agent in agents):
                raise TypeError("all items in agents must be Agent objects")

            agent_names = [agent.name for agent in agents]

            if agent_names:
                placeholders = ", ".join("?" for _ in agent_names)
                sql += f" AND agent_name IN ({placeholders})"
                params.extend(agent_names)

        if model is not None:
            sql += " AND model = ?"
            params.append(model)

        if reasoning_effort is not None:
            sql += " AND reasoning_effort = ?"
            params.append(reasoning_effort)

        cur.execute(sql, tuple(params))
        row = cur.fetchone()

        return {
            "input_tokens": int(row[0] or 0),
            "output_tokens": int(row[1] or 0),
            "output_reasoning_tokens": int(row[2] or 0),
            "total_tokens": int(row[3] or 0),
        }

    def create_models(self):

        Message('LLMRequest', LLMRequest, 'Request')
        Message('LLMResponse', LLMResponse, 'Response')
        Message('LLMOutput', LLMOutput, 'Response')
        Message('Request', Request, message_type='Request')
        Message('Response', Response, 'Response')

    def initialize_OpenAI(self):

        cls_text = f'''Class Agents.Operation.OpenAI Extends Ens.BusinessOperation
        {{
        Parameter INVOCATION = "Queue";

        XData MessageMap
        {{
        <MapItem MessageType="Agents.Message.LLMRequest">
        <Method>SendLLM</Method>
        </MapItem>
        }}

        ClassMethod PostResponses(model As %String, inputJson As %String, reasoningDetailedJson As %String, apiKey As %String, responseType As %String, reasoningEffort As %String, Output pDurationMs As %BigInt = "") As %String
        {{
            Set contentSchema = ##class(Agents.Utils.Production).BuildContentSchema(responseType)
            Set contentSchemaText = contentSchema.%ToJSON()

            Set mergedInputJson = ##class(Agents.Utils.Production).MergeOutputWithReasoningDetailed(
                inputJson,
                reasoningDetailedJson
            )
            If mergedInputJson="" {{
                Set mergedInputJson = inputJson
            }}

            Set originalInput = ##class(%DynamicArray).%FromJSON(mergedInputJson)
            Set finalInput = ##class(%DynamicArray).%New()

            Set sys = ##class(%DynamicObject).%New()
            Do sys.%Set("role", "system")
            Do sys.%Set("content", "Return JSON with exactly these fields: IsTool, Toolkit, Tool, Content. "_
                "If IsTool is false, Content must be a JSON string whose parsed value conforms exactly to this schema: "_contentSchemaText_" "_
                "If IsTool is true, Content must be a JSON string of tool parameters. "_
                "Toolkit and Tool must be empty strings when IsTool is false.")
            Do finalInput.%Push(sys)

            For i=0:1:originalInput.%Size()-1 {{
                Set item = originalInput.%Get(i)

                If $IsObject(item) {{
                    Set itemJSON = item.%ToJSON()
                    Set firstChar = $Extract(itemJSON,1)

                    If firstChar="{{" {{
                        Set itemCopy = ##class(%DynamicObject).%FromJSON(itemJSON)
                        Do finalInput.%Push(itemCopy)
                    }} ElseIf firstChar="[" {{
                        Set itemCopy = ##class(%DynamicArray).%FromJSON(itemJSON)
                        Do finalInput.%Push(itemCopy)
                    }} Else {{
                        Do finalInput.%Push(itemJSON)
                    }}
                }} Else {{
                    Do finalInput.%Push(item)
                }}
            }}

            Set body = ##class(%DynamicObject).%New()
            Do body.%Set("model", model)
            Do body.%Set("input", finalInput)

            Set reasoning = ##class(%DynamicObject).%New()
            Do reasoning.%Set("effort", reasoningEffort)
            Do reasoning.%Set("summary", "detailed")
            Do body.%Set("reasoning", reasoning)

            Set include = ##class(%DynamicArray).%New()
            Do include.%Push("reasoning.encrypted_content")
            Do body.%Set("include", include)

            Set fmt = ##class(%DynamicObject).%New()
            Do fmt.%Set("type", "json_schema")
            Do fmt.%Set("name", "LLMOutput")
            Do fmt.%Set("strict", 1)
            Do fmt.%Set("schema", ##class(Agents.Utils.Production).BuildLLMOutputSchema())

            Set text = ##class(%DynamicObject).%New()
            Do text.%Set("format", fmt)
            Do body.%Set("text", text)
            Do body.%Set("max_output_tokens", 8000)

            Set json = body.%ToJSON()
            Set json = $Replace(json, """strict"":1", """strict"":true")
            Set json = $Replace(json, """strict"":0", """strict"":false")
            Set json = $Replace(json, """additionalProperties"":1", """additionalProperties"":true")
            Set json = $Replace(json, """additionalProperties"":0", """additionalProperties"":false")

            Set apiKey = $ZSTRIP($Get(apiKey), "<>W")
            Set apiKey = $TR(apiKey, $CHAR(13,10), "")

            If apiKey="" {{
                Quit "{{""error"":""missing_api_key""}}"
            }}

            Set http = ##class(%Net.HttpRequest).%New()
            Set http.Https = 1
            Set http.Server = "api.openai.com"
            Set http.Port = 443
            Set http.SSLConfiguration = "OpenAI"
            Set http.Timeout = 240

            Do http.SetHeader("Authorization", "Bearer "_apiKey)
            Set http.ContentType = "application/json"
            Set http.ContentCharset = "UTF-8"
            Do http.SetHeader("Accept", "application/json")

            Do http.EntityBody.Write(json)
            Do http.EntityBody.Rewind()

            Set tStart = $ZHOROLOG
            Set sc = http.Post("/v1/responses")
            Set tEnd = $ZHOROLOG
            Set pDurationMs = ##class(Agents.Utils.Common).ElapsedMs(tStart, tEnd)
            If $$$ISERR(sc) {{
                Set err = $SYSTEM.Status.GetErrorText(sc)
                Set sslcfg = http.SSLConfiguration
                Quit "{{""error"":""http_post_failed"",""detail"":"""_$ZCONVERT(err,"O","JSON")_""",""server"":"""_http.Server_""",""port"":"_http.Port_",""https"":"_http.Https_",""sslConfig"":"""_sslcfg_"""}}"
            }}

            If '$IsObject(http.HttpResponse) {{
                Quit "{{""error"":""no_http_response""}}"
            }}

            Set statusCode = +http.HttpResponse.StatusCode
            Set respBody = ##class(Agents.Utils.Common).ToText(http.HttpResponse.Data)

            If (statusCode < 200) || (statusCode > 299) {{
                Set obj = ##class(%DynamicObject).%New()
                Do obj.%Set("error", "http_error")
                Do obj.%Set("status_code", statusCode)
                Do obj.%Set("detail", respBody)
                Quit obj.%ToJSON()
            }}

            Quit respBody
        }}



        Method SendLLM(pRequest As Agents.Message.LLMRequest, Output pResponse As Agents.Message.LLMResponse) As %Status
        {{
            Set pResponse = ##class(Agents.Message.LLMResponse).%New()
            Set sc = $$$OK
            Set raw = ""
            Set outText = ""
            Set hasError = 0
            Set usageJSON = ""
            Set durationMs = ""
            Set reasoningSummary = ""
            Set responseOutput = ""
            Set reasoningDetailed = ""

            Set apiKey = ##class(Ens.Config.Credentials).GetValue("OPENAI_API_KEY", "Password")
            Set apiKey = $ZSTRIP($Get(apiKey), "<>W")
            Set apiKey = $TR(apiKey, $CHAR(13,10), "")

            Set raw = ..PostResponses(pRequest.Model, pRequest.Chat, pRequest.ReasoningDetailed, apiKey, pRequest.ResponseType, pRequest.ReasoningEffort, .durationMs)

            Try {{
                Set rawObj = ##class(%DynamicObject).%FromJSON(raw)

                Set hasTopErr = 0
                Set err = ""
                Set detail = ""

                If rawObj.%IsDefined("error") {{
                    Set err = rawObj.%Get("error")
                    If err'="" {{
                        Set hasTopErr = 1
                    }}
                }}

                If rawObj.%IsDefined("detail") {{
                    Set detail = rawObj.%Get("detail")
                }}

                If hasTopErr {{
                    Set msg = "OpenAI returned error: "_err
                    If detail'="" {{
                        Set msg = msg_" | detail: "_detail
                    }}
                    Set sc = $$$ERROR($$$GeneralError, msg)
                    Set hasError = 1
                }} ElseIf rawObj.%IsDefined("status") {{
                    Set respStatus = rawObj.%Get("status")
                    If (respStatus'="completed")&&(respStatus'="in_progress") {{
                        Set sc = $$$ERROR($$$GeneralError, "Unexpected OpenAI response status: "_respStatus_" Raw="_raw)
                        Set hasError = 1
                    }}
                }}
            }} Catch ex {{
                // ignore parse failure on raw here
            }}
            If hasError Quit sc
            If hasError Quit sc

            If $IsObject(rawObj), rawObj.%IsDefined("usage") {{
                Try {{
                    Set usageObj = rawObj.%Get("usage")
                    If durationMs'="" {{
                        Do usageObj.%Set("duration_ms", +durationMs)
                    }}
                    Set usageJSON = usageObj.%ToJSON()
                }} Catch ex {{
                    Set usageJSON = ""
                }}
            }}
            Set reasoningSummary = ##class(Agents.Utils.Production).ExtractReasoningSummary(raw)
            Set responseOutput = ##class(Agents.Utils.Production).ExtractOutputItems(raw)
            Set reasoningDetailed = ##class(Agents.Utils.Production).ExtractReasoningDetailed(raw)
            Set outText = ##class(Agents.Utils.Production).ExtractOutputText(raw)
            If outText="" {{
                Set sc = $$$ERROR($$$GeneralError, "No output_text returned by model. Raw="_raw)
                Quit sc
            }}

            Set hasError = 0
            Try {{
                Set obj = ##class(%DynamicObject).%FromJSON(outText)

                Set pResponse.IsTool = +obj.%Get("IsTool")
                Set pResponse.Toolkit = ##class(Agents.Utils.Common).ToJSONString(obj.%Get("Toolkit"))
                Set pResponse.Tool = ##class(Agents.Utils.Common).ToJSONString(obj.%Get("Tool"))
                Set pResponse.Content = ##class(Agents.Utils.Common).ToJSONString(obj.%Get("Content"))
                Set pResponse.Usage = usageJSON
                Set pResponse.ReasoningSummary = reasoningSummary
                Set pResponse.ResponseOutput = responseOutput
                Set pResponse.ReasoningDetailed = reasoningDetailed
            }}Catch ex {{
                Set sc = $$$ERROR($$$GeneralError, "Model returned invalid wrapper JSON: "_outText)
                Set hasError = 1
            }}
            If hasError Quit sc

            Quit $$$OK
        }}
        }}
        '''
        create_class('Agents.Operation.OpenAI', cls_text)

    def build(self):
        ensure_common_utils()
        ensure_production_utils()
        self.create_models()
        self.ensure_tool_usage_table()
        self.ensure_llm_usage_table()
        self.initialize_OpenAI()

        prod_xml = f'''<Production Name="{self.name}" LogGeneralTraceEvents="false">
        <Description></Description>
        <ActorPoolSize>1</ActorPoolSize>
        '''

        toolkit_names = set()

        for agent in self.agents:
            agent.ensure_runtime_classes()

            prod_xml += f'<Item Name="{agent.name}Gateway" ClassName="Agents.Gateway.{agent.name}Service" PoolSize="1" Enabled="true"/>\n' + \
            f'<Item Name="{agent.name}" ClassName="Agents.Process.{agent.name}" PoolSize="1" Enabled="true"/>\n'

            for toolkit in (agent.toolkits or []):
                if toolkit.name not in toolkit_names:
                    toolkit_names.add(toolkit.name)
                    prod_xml += (
                        f'<Item Name="{toolkit.name}" '
                        f'ClassName="Agents.Operation.Toolkit{toolkit.name}" '
                        f'PoolSize="1" Enabled="true"/>\n'
                    )

        prod_xml += '<Item Name="OpenAI" ClassName="Agents.Operation.OpenAI" PoolSize="1" Enabled="true"/>\n</Production>'

        cls_text = f"""Class {self.name} Extends Ens.Production
        {{
        XData ProductionDefinition
        {{
        {prod_xml}
        }}
        }}
        """

        create_class(self.name, cls_text)
        self.create_dispatch()
        self.create_admin()

    def start(self):
        irispy = get_connection(True)
        sc = irispy.classMethodValue("Ens.Director", "StopProduction", 10, 1)
        if sc != 1:
            print(irispy.classMethodValue("%SYSTEM.Status","GetErrorText", sc))

        sc = irispy.classMethodValue("Ens.Director", "StartProduction", f'User.{self.name}')
        if sc != 1:
            raise RuntimeError(irispy.classMethodValue("%SYSTEM.Status", "GetErrorText", sc))

        initialized = set()
        for agent in self.agents:
            for toolkit in (agent.toolkits or []):
                if toolkit.name in initialized:
                    continue
                initialized.add(toolkit.name)

                session_ref = iris.IRISReference("")
                cls_name = f"Agents.Operation.Toolkit{toolkit.name}"
                sc = irispy.classMethodValue(cls_name, "InitializeSession", session_ref)
                if sc != 1:
                    raise RuntimeError(
                        f"Failed to initialize toolkit session for {toolkit.name}: " +
                        irispy.classMethodValue("%SYSTEM.Status", "GetErrorText", sc)
                    )

        print("Created/compiled/started:", self.name)

    def create_dispatch(self):
        cls_text = f'''
        Class Agents.REST.Dispatch.{self.name} Extends %CSP.REST
        {{
        Parameter ProductionName = "User.{self.name}";

        XData UrlMap
        {{
        <Routes>
            <Route Url="/:agentName" Method="POST" Call="Agent" Cors="false" />
        </Routes>
        }}

        ClassMethod Agent(agentName As %String) As %Status
        {{
            Set %response.ContentType="application/json"

            Set rs = ##class(%ResultSet).%New("Ens.Config.Production:EnumerateConfigItemNames")
            Do rs.Execute(..#ProductionName, "")

            Set found = 0
            While rs.%Next() {{
                Set cls = rs.GetData(3)
                If cls = ("Agents.Gateway."_agentName_"Service") {{
                    Set found = 1
                    Quit
                }}
            }}

            If 'found {{
                Set %response.Status = 404
                Write "{{""error"":""Agent not available""}}"
                Quit $$$OK
            }}

            Set body = %request.Content.Read()
            If body = "" {{
                Set %response.Status = "400 Bad Request"
                Quit $$$OK
            }}

            Set req = ##class(Agents.Message.Request).%New()
            Do req.%JSONImport(body)

            Set itemName = agentName _ "Gateway"
            
            Set svc = ""
            Set sc = ##class(Ens.Director).CreateBusinessService(itemName, .svc)
            If sc '= 1 {{
                Set err = ##class(%SYSTEM.Status).GetErrorText(sc)
                Set %response.Status = "500 Internal Server Error"
                Write "{{""error"":""CreateBusinessService failed"",""code"":"""_sc_""",""message"":"""_err_"""}}"
                Quit $$$OK
            }}
            If '$IsObject(svc) {{
                Set %response.Status = "500 Internal Server Error"
                Write "{{""error"":""CreateBusinessService returned non-object"",""item"":"""_itemName_"""}}"
                Quit $$$OK
            }}

            // report the created instance class (helpful to verify it's the expected runtime object)
            Set svcClass = $CLASSNAME(svc)

            // --- attempt the SendRequestSync, capturing platform error state on throw
            Set sc = 0
            Set zerr = ""
            Set zstatus = ""
            Try {{
                Set sc = svc.SendRequestSync(agentName, .req, .resp)
            }} Catch {{
                // capture both error variables (one of them usually contains useful info)
                Set zerr = $ZERROR
                Set zstatus = $ZSTATUS
            }}

            If sc '= 1 {{
                // If the Try/Catch captured something, return it
                If zerr'=""!(zstatus'="") {{
                    Set %response.Status = "500 Internal Server Error"
                    Write "{{""error"":""SendRequestSync threw"",""agent"":"""_agentName_""",""svcClass"":"""_svcClass_""",""zerr"":"""_zerr_""",""zstatus"":"""_zstatus_"""}}"
                    Quit $$$OK
                }}
                // Otherwise we have a numeric status: translate it
                Set err = ##class(%SYSTEM.Status).GetErrorText(sc)
                Set %response.Status = "500 Internal Server Error"
                Write "{{""error"":""SendRequestSync failed"",""agent"":"""_agentName_""",""svcClass"":"""_svcClass_""",""code"":"""_sc_""",""message"":"""_err_"""}}"
                Quit $$$OK
            }}

            Set %response.Status = 200
            Set json=""
            If $IsObject(resp) {{
                Do resp.%JSONExportToString(.json)
            }} Else {{
                Set json=""+resp
            }}

            Try {{
                Set obj = ##class(%DynamicObject).%FromJSON(json)
                If obj.%IsDefined("ChatId"), (obj.%Get("ChatId")="") {{
                    Do obj.%Remove("ChatId")
                    Set json = obj.%ToJSON()
                }}
            }} Catch ex {{
                // ignore just return original json
            }}


            Write json
            Quit $$$OK
        }}
        }}
        '''
        create_class(f'Agents.REST.Dispatch.{self.name}', cls_text)


    def create_admin(self):
        cls_text = r'''
            Class Agents.Admin
            {

                ClassMethod DeleteWebApp(pPath As %String) As %Status
                {
                    Set oldNS = $Namespace
                    Set $Namespace = "%SYS"

                    If '##class(Security.Applications).Exists(pPath) {
                        Set $Namespace = oldNS
                        Quit $$$OK
                    }

                    Set sc = ##class(Security.Applications).Delete(pPath)
                    Set $Namespace = oldNS
                    Quit sc
                }

                ClassMethod DeleteTLSConfigIfExists(pName As %String) As %Status
                {
                    Set oldNS = $Namespace
                    Set $Namespace = "%SYS"

                    If '##class(Security.SSLConfigs).Exists(pName) {
                        Set $Namespace = oldNS
                        Quit $$$OK
                    }

                    Set sc = ##class(Security.SSLConfigs).Delete(pName)
                    Set $Namespace = oldNS
                    Quit sc
                }

                ClassMethod DeleteCredentialIfExists(pName As %String) As %Status
                {
                    Quit ##class(Ens.Config.Credentials).DeleteCredential(pName)
                }

                ClassMethod DeleteClassIfExists(pClassName As %String) As %Status
                {
                    New $Namespace
                    Set $Namespace = "Agents"
                    Set sc = $$$OK

                    If ##class(%Dictionary.ClassDefinition).%ExistsId(pClassName) {
                        Try {
                            Set sc = $SYSTEM.OBJ.Delete(pClassName)
                        } Catch ex {
                            Set sc = ex.AsStatus()
                        }
                        Quit sc
                    }

                    If ##class(%Dictionary.CompiledClass).%ExistsId(pClassName) {
                        Try {
                            Set sc = $SYSTEM.OBJ.Delete(pClassName)
                        } Catch ex {
                            Set sc = ex.AsStatus()
                        }
                    }

                    Quit sc
                }

                ClassMethod EnsureTLSConfigForOpenAI(pName As %String = "OpenAI") As %Status
                {
                    Set oldNS = $Namespace
                    Set $Namespace = "%SYS"
                    Set sc = $$$OK

                    Try {
                        Kill props
                        Set props("Description") = "TLS client config for OpenAI API"
                        Set props("Enabled") = 1
                        Set props("Type") = 0
                        Set props("VerifyPeer") = 1
                        Set props("CAFile") = "%OSCertificateStore"

                        If ##class(Security.SSLConfigs).Exists(pName) {
                            Set sc = ##class(Security.SSLConfigs).Modify(pName, .props)
                        } Else {
                            Set sc = ##class(Security.SSLConfigs).Create(pName, .props)
                        }

                        If $$$ISOK(sc) {
                            Set cfg = ##class(Security.SSLConfigs).%OpenId(pName)
                            If '$IsObject(cfg) {
                                Set sc = $$$ERROR($$$GeneralError, "Unable to open TLS config "_pName)
                            } Else {
                                Set sc = cfg.Activate()
                            }
                        }
                    } Catch ex {
                        Set sc = ex.AsStatus()
                    }

                    Set $Namespace = oldNS
                    Return sc
                }

                /// Returns the web application definition as JSON
                ClassMethod GetWebAppJSON(pPath As %String) As %String
                {
                    Set oldNS = $Namespace
                    Set $Namespace = "%SYS"

                    Set sc = ##class(Security.Applications).Get(pPath, .props)
                    If sc '= 1 {
                        Set $Namespace = oldNS
                        Quit ""
                    }

                    Set obj = ##class(%DynamicObject).%New()
                    Set key = ""
                    For  Set key = $Order(props(key)) Quit:key=""  Do obj.%Set(key, props(key))

                    Set out = obj.%ToJSON()
                    Set $Namespace = oldNS
                    Quit out
                }

                /// Modifies selected web app properties
                ClassMethod ModifyWebAppProps(pPath As %String, pCSRF As %Integer, pUseCookies As %Integer, pMatchRoles As %String) As %Status
                {
                    Set oldNS = $Namespace
                    Set $Namespace = "%SYS"

                    Set sc = ##class(Security.Applications).Get(pPath, .props)
                    If sc '= 1 {
                        Set $Namespace = oldNS
                        Quit $$$ERROR($$$GeneralError,"web-app-not-found")
                    }

                    Set props("CSRFToken") = pCSRF
                    Set props("UseCookies") = pUseCookies
                    If $Get(pMatchRoles)'="" {
                        Set props("MatchRoles") = pMatchRoles
                    }

                    Set sc = ##class(Security.Applications).Modify(pPath, .props)
                    Set $Namespace = oldNS
                    Quit sc
                }

                ClassMethod EnsureWebApp(pPath As %String, pNamespace As %String, pDispatchClass As %String) As %Status
                {
                    Set oldNS = $Namespace
                    Set $Namespace = "%SYS"

                    // If it exists, we're done
                    If ##class(Security.Applications).Exists(pPath) {
                        Set $Namespace = oldNS
                        Quit $$$OK
                    }

                    Kill props
                    Set props("NameSpace") = pNamespace
                    Set props("DispatchClass") = pDispatchClass
                    Set props("Enabled") = 1
                    Set props("IsNameSpaceDefault") = 0
                    Set props("AutheEnabled") = 32
                    Set props("CSRFToken") = 0
                    Set props("UseCookies") = 0

                    Set sc = ##class(Security.Applications).Create(pPath, .props)
                    Set $Namespace = oldNS
                    Quit sc
                }

                ClassMethod SetCredential(name As %String, username As %String, password As %String) As %Status
                {
                    // pOverwrite=1 so you can re-run your production build without manual portal clicks
                    Quit ##class(Ens.Config.Credentials).SetCredential(name, username, password, 1)
                }

                ClassMethod SetCPFConfig(pName As %String, pValue As %String) As %Status
                {
                    Set oldNS = $Namespace
                    Set sc = $$$OK
                    Set $Namespace = "%SYS"

                    Try {
                        Kill props
                        Set props(pName) = pValue
                        Set sc = ##class(Config.config).Modify(.props)  // writes + activates by default flags
                    }
                    Catch ex {
                        Set sc = ex.AsStatus()
                    }

                    Set $Namespace = oldNS
                    Return sc
                }
            }
            '''
        create_class("Agents.Admin", cls_text)

        # Set up Web App

        irispy = get_connection(True)
        sc = irispy.classMethodValue('Agents.Admin', 'EnsureWebApp', f'/csp/agents/{self.name}', 'Agents', f'Agents.REST.Dispatch.{self.name}')
        if sc != 1:
            raise RuntimeError(irispy.classMethodValue("%SYSTEM.Status", "GetErrorText", sc))
        else:
            print(f'Created Web App successfully at /csp/agents/{self.name}')

        # Set up TLSConfig

        sc = irispy.classMethodValue("Agents.Admin", "EnsureTLSConfigForOpenAI", "OpenAI")
        if sc != 1:
            raise RuntimeError(irispy.classMethodValue("%SYSTEM.Status", "GetErrorText", sc))
        else:
            print("Configured TLS profile 'OpenAI'")

        # Set OpenAI API Key as Credential

        sc = irispy.classMethodValue(
            "Agents.Admin",
            "SetCredential",
            "OPENAI_API_KEY",          # credential name
            "OPENAI_API_KEY",          # username placeholder
            self.openai_api_key if self.openai_api_key else os.environ['OPENAI_API_KEY']
        )
        if sc != 1:
            raise RuntimeError(irispy.classMethodValue("%SYSTEM.Status", "GetErrorText", sc))
        else:
            print('Set OpenAI API Key Successfully!')

    def __repr__(self):
        toolkits = sorted({
            toolkit.name
            for agent in self.agents
            for toolkit in (agent.toolkits or [])
        })

        return f'''roduction: {self.name}
    Agents: {[agent for agent in self.agents]}
    Tools: {toolkits if toolkits else 'No configured tools'}'''

    def cleanup(self):
        """
        Remove only production-owned artifacts.

        This removes:
        - Web app: /csp/agents/<ProductionName>
        - Dispatch class: Agents.REST.Dispatch.<ProductionName>

        It does NOT remove:
        - shared classes
        - message classes
        - utility classes
        - LLM/toolkit classes
        - agent-owned classes
        - TLS config / credentials
        """
        irispy = get_connection(True)
        errors = []

        web_app_path = f'/csp/agents/{self.name}'
        try:
            sc = irispy.classMethodValue("Agents.Admin", "DeleteWebApp", web_app_path)
            if sc != 1:
                errors.append(
                    f"DeleteWebApp({web_app_path}) failed: "
                    f"{irispy.classMethodValue('%SYSTEM.Status', 'GetErrorText', sc)}"
                )
        except Exception as e:
            errors.append(f"DeleteWebApp({web_app_path}) raised: {e}")

        try:
            sc = irispy.classMethodValue(
                "Agents.Admin",
                "DeleteClassIfExists",
                f"Agents.REST.Dispatch.{self.name}"
            )
            if sc != 1:
                errors.append(
                    f"DeleteClassIfExists(Agents.REST.Dispatch.{self.name}) failed: "
                    f"{irispy.classMethodValue('%SYSTEM.Status', 'GetErrorText', sc)}"
                )
        except Exception as e:
            errors.append(f"DeleteClassIfExists(Agents.REST.Dispatch.{self.name}) raised: {e}")

        if errors:
            raise RuntimeError("Cleanup encountered errors:\n" + "\n".join(errors))

    def delete(self):
        """
        Stop and delete the production class (User.<ProductionName>).
        This will NOT remove your agent/message classes.

        Behavior:
        - Attempts to stop the production.
        - Attempts to DeleteProduction().
        - If DeleteProduction fails (often due to runtime state), attempts CleanProduction()
        and retries DeleteProduction once more.
        - Raises RuntimeError if final DeleteProduction still fails.
        """
        irispy = get_connection(True)
        prod_id = f'User.{self.name}'

        # 1) Try to stop the production (idempotent if already stopped)
        sc = irispy.classMethodValue("Ens.Director", "StopProduction", 10, 1)
        if sc != 1:
            try:
                print("StopProduction:", irispy.classMethodValue("%SYSTEM.Status", "GetErrorText", sc))
            except Exception:
                print("StopProduction returned status", sc)

        # 2) Try to delete the production
        sc = irispy.classMethodValue("Ens.Director", "DeleteProduction", prod_id, 0)
        if sc == 1:
            print("Deleted production:", prod_id)
            self.cleanup()
            print(f"Cleaned up production-owned artifacts for: {self.name}")
            return

        # 3) If delete failed, try a force path: CleanProduction then DeleteProduction
        #    (CleanProduction is destructive: it purges runtime state)
        try:
            sc_clean = irispy.classMethodValue("Ens.Director", "CleanProduction", prod_id)
            if sc_clean != 1:
                try:
                    print("CleanProduction:", irispy.classMethodValue("%SYSTEM.Status", "GetErrorText", sc_clean))
                except Exception:
                    print("CleanProduction returned status", sc_clean)
        except Exception as e:
            # If CleanProduction is not available or threw, log and continue to retry delete
            print("CleanProduction attempt raised:", e)

        # Retry delete after cleaning
        sc = irispy.classMethodValue("Ens.Director", "DeleteProduction", prod_id, 0)
        if sc == 1:
            print("Deleted production after cleaning:", prod_id)
            self.cleanup()
            print(f"Cleaned up production-owned artifacts for: {self.name}")
            return

        # Final failure -> raise with readable error
        try:
            errmsg = irispy.classMethodValue("%SYSTEM.Status", "GetErrorText", sc)
        except Exception:
            errmsg = f"DeleteProduction returned status {sc}"
        raise RuntimeError(f"DeleteProduction failed for {prod_id}: {errmsg}")
