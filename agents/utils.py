import iris
from dotenv import load_dotenv
import os

load_dotenv()

SCHEMA_DEFINITIONS = {
    'Prompt': {
        'create': '''
            CREATE TABLE Agents.Prompt (
                prompt_id VARCHAR(200) NOT NULL,
                prompt_text VARCHAR(200) NOT NULL,
                version INT NOT NULL,
                PRIMARY KEY (prompt_id, version)
            )
        ''',
    },
    'Agent': {
        'create': '''
            CREATE TABLE Agents.Agent (
                agent_name VARCHAR(200) NOT NULL PRIMARY KEY,
                description VARCHAR(4000),
                system_prompt_id VARCHAR(200),
                model VARCHAR(200),
                response_format VARCHAR(4000),
                reasoning_effort VARCHAR(50),
                persist_reasoning INTEGER
            )
        ''',
    },
    'AgentToolkit': {
        'create': '''
            CREATE TABLE Agents.AgentToolkit (
                agent_name VARCHAR(200) NOT NULL,
                toolkit_id VARCHAR(200) NOT NULL,
                PRIMARY KEY (agent_name, toolkit_id)
            )
        ''',
    },
    'Toolkit': {
        "create": '''
            CREATE TABLE Agents.Toolkit (
                toolkit_id VARCHAR(200) NOT NULL PRIMARY KEY,
                toolkit_url VARCHAR(1000) NOT NULL
            )
        ''',
    },
    'Chat': {
        'create': '''
            CREATE TABLE Agents.Chat (
                message_id BIGINT IDENTITY PRIMARY KEY,
                id VARCHAR(200) NOT NULL,
                workflow VARCHAR(200),
                message_role VARCHAR(50) NOT NULL,
                message VARCHAR(50000) NOT NULL,
                reasoning_summary VARCHAR(50000),
                reasoning_detailed VARCHAR(50000)
            )
        ''',
        'indexes': [
            'CREATE INDEX idx_chat_id_msgid ON Agents.Chat (id, message_id)',
            'CREATE INDEX idx_chat_id_workflow_msgid ON Agents.Chat (id, workflow, message_id)',
        ],
    },
    'ToolUsage': {
        'create': '''
            CREATE TABLE Agents.ToolUsage (
                usage_id BIGINT IDENTITY PRIMARY KEY,
                usage_ts TIMESTAMP NOT NULL,
                chat_id VARCHAR(200),
                workflow VARCHAR(200),
                agent_name VARCHAR(200) NOT NULL,
                toolkit VARCHAR(200) NOT NULL,
                tool_name VARCHAR(200) NOT NULL,
                request_payload VARCHAR(50000) NOT NULL,
                response_ok INTEGER NOT NULL,
                response_payload VARCHAR(50000) NOT NULL
            )
        ''',
        'indexes': [
            'CREATE INDEX idx_toolusage_chat_ts ON Agents.ToolUsage (chat_id, usage_ts)',
            'CREATE INDEX idx_toolusage_chat_workflow_ts ON Agents.ToolUsage (chat_id, workflow, usage_ts)',
        ],
    },
    'Usage': {
        'create': '''
            CREATE TABLE Agents.Usage (
                usage_id BIGINT IDENTITY PRIMARY KEY,
                usage_ts TIMESTAMP NOT NULL,
                chat_id VARCHAR(200),
                workflow VARCHAR(200),
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
        ''',
        'indexes': [
            'CREATE INDEX idx_usage_chat_ts ON Agents.Usage (chat_id, usage_ts)',
            'CREATE INDEX idx_usage_chat_workflow_ts ON Agents.Usage (chat_id, workflow, usage_ts)',
            'CREATE INDEX idx_usage_agent_ts ON Agents.Usage (agent_name, usage_ts)',
            'CREATE INDEX idx_usage_prod_ts ON Agents.Usage (production_name, usage_ts)',
            'CREATE INDEX idx_usage_model_ts ON Agents.Usage (model, usage_ts)',
            'CREATE INDEX idx_usage_workflow_ts ON Agents.Usage (workflow, usage_ts)',
        ],
    },
}

def connect(namespace: str, obj: bool = False):
    conn = iris.connect(
        hostname=os.environ['IRIS_HOSTNAME'],
        port=int(os.environ['IRIS_PORT']),
        namespace=namespace,
        username=os.environ['IRIS_USERNAME'],
        password=os.environ['IRIS_PASSWORD'],
    )
    return iris.createIRIS(conn) if obj else conn


def ensure_agents_namespace(namespace: str = 'Agents') -> None:
    irispy = connect('%SYS', obj=True)

    exists = int(irispy.classMethodValue('%SYS.Namespace', 'Exists', namespace))
    if exists != 1:
        irispy.classMethodObject('%SQL.Statement', '%ExecDirect', '', f'CREATE DATABASE {namespace}')

    nsiris = connect(namespace, obj=True)
    has_req = int(nsiris.classMethodValue('%Dictionary.ClassDefinition', '%ExistsId', 'Ens.Request'))
    has_bo = int(nsiris.classMethodValue('%Dictionary.ClassDefinition', '%ExistsId', 'Ens.BusinessOperation'))

    if not (has_req == 1 and has_bo == 1):
        sc = irispy.classMethodValue('%Library.EnsembleMgr', 'EnableNamespace', namespace, 1)
        if sc != 1:
            raise RuntimeError(irispy.classMethodValue('%SYSTEM.Status', 'GetErrorText', sc))

def ensure_schema(*table_names: str) -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        '''
        SELECT TABLE_NAME
        FROM INFORMATION_SCHEMA.Tables
        WHERE TABLE_TYPE='BASE TABLE'
        AND TABLE_SCHEMA='Agents'
        '''
    )
    existing_tables = {row[0] for row in cur.fetchall()}

    if not table_names:
        names = list(SCHEMA_DEFINITIONS.keys())
    else:
        names = list(dict.fromkeys(table_names))

    for name in names:
        if name not in SCHEMA_DEFINITIONS:
            raise KeyError(f'Unknown schema definition: {name}')

        definition = SCHEMA_DEFINITIONS[name]

        if name not in existing_tables:
            cur.execute(definition['create'])
            conn.commit()
            existing_tables.add(name)

        for index_sql in definition.get('indexes', []):
            try:
                cur.execute(index_sql)
                conn.commit()
            except Exception:
                pass


def get_connection(obj=False, namespace='Agents'):
    return connect(namespace, obj)

def create_class(cls_name: str, cls_text: str) -> None:
    irispy = get_connection(True)

    stream = irispy.classMethodObject('%Stream.GlobalCharacter', '%New')
    stream.invoke('Write', cls_text)
    stream.invoke('Rewind')

    errorlog = iris.IRISReference(None)
    loadedlist = iris.IRISReference(None)

    sc = irispy.classMethodValue(
        '%SYSTEM.OBJ', 'LoadStream',
        stream,
        'ck',
        errorlog,
        loadedlist,
        0,
        '',
        f'{cls_name}.cls',
        'UTF-8'
    )

    if sc != 1:
        raise RuntimeError(irispy.classMethodValue('%SYSTEM.Status', 'GetErrorText', sc))

def ensure_common_utils():
    cls_text = r'''Class Agents.Utils.Common Extends %RegisteredObject
    {

        ClassMethod GetLatestReasoningDetailed(chatId As %String) As %String
        {
            Set reasoningDetailed = ""
            If $Get(chatId)="" {
                Quit ""
            }

            &sql(
                SELECT TOP 1 reasoning_detailed
                INTO :reasoningDetailed
                FROM Agents.Chat
                WHERE id = :chatId
                AND message_role = 'assistant'
                AND reasoning_detailed IS NOT NULL
                AND reasoning_detailed <> ''
                ORDER BY message_id DESC
            )

            If SQLCODE'=0 {
                Quit ""
            }

            Quit $Get(reasoningDetailed)
        }

        ClassMethod AppendChatReturnMessageId(
            chatId As %String,
            messageRole As %String,
            msg As %String,
            reasoningSummary As %String = "",
            reasoningDetailed As %String = "",
            workflow As %String = "",
            Output pMessageId As %BigInt
        ) As %Status
        {
            Set pMessageId = ""
            If $Get(chatId)="" Quit $$$OK

            &sql(INSERT INTO Agents.Chat
                (id, workflow, message_role, message, reasoning_summary, reasoning_detailed)
                VALUES
                (:chatId, :workflow, :messageRole, :msg, :reasoningSummary, :reasoningDetailed))

            If SQLCODE<0 Quit $$$ERROR($$$GeneralError,"Failed to append chat row")

            &sql(SELECT LAST_IDENTITY() INTO :pMessageId)

            If SQLCODE<0 {
                Quit $$$ERROR($$$GeneralError,"Chat row inserted but failed to fetch message_id")
            }

            Quit $$$OK
        }

        ClassMethod ElapsedMs(pStart As %String, pEnd As %String) As %BigInt
        {
            Set ms = 0

            Try {
                Set start = +pStart
                Set finish = +pEnd
                Set ms = (finish - start) * 1000

                If ms < 0 {
                    Set ms = 0
                } Else {
                    Set ms = +ms
                }
            } Catch ex {
                Set ms = 0
            }

            Quit ms
        }

        ClassMethod GetRunningProductionName() As %String
        {
            Set prodName = ""
            Set state = 0
            Set sc = ##class(Ens.Director).GetProductionStatus(.prodName, .state, 10, 0)
            If sc '= 1 {
                Quit ""
            }
            If prodName["." {
                Quit $Piece(prodName,".",*)
            }
            Quit prodName
        }
        
        ClassMethod LogLLMUsage(
            pChatId As %String,
            pMessageId As %BigInt,
            pAgentName As %String,
            pProductionName As %String,
            pModel As %String,
            pReasoningEffort As %String,
            pUsageJSON As %String,
            pWorkflow As %String = ""
        ) As %Status
        {
            Set sc = $$$OK
            Set ts = $ZDATETIME($HOROLOG,3,1,3)

            Set inputTokens = ""
            Set outputTokens = ""
            Set totalTokens = ""
            Set inputCachedTokens = ""
            Set inputAudioTokens = ""
            Set outputAudioTokens = ""
            Set outputReasoningTokens = ""
            Set durationMs = ""

            If $Get(pUsageJSON)="" {
                Quit $$$OK
            }

            Try {
                Set usage = ##class(%DynamicObject).%FromJSON(pUsageJSON)

                If usage.%IsDefined("input_tokens") {
                    Set inputTokens = +usage.%Get("input_tokens")
                }
                If usage.%IsDefined("output_tokens") {
                    Set outputTokens = +usage.%Get("output_tokens")
                }
                If usage.%IsDefined("total_tokens") {
                    Set totalTokens = +usage.%Get("total_tokens")
                }
                If usage.%IsDefined("duration_ms") {
                    Set durationMs = +usage.%Get("duration_ms")
                }

                If usage.%IsDefined("input_tokens_details") {
                    Set itd = usage.%Get("input_tokens_details")
                    If $IsObject(itd) {
                        If itd.%IsDefined("cached_tokens") {
                            Set inputCachedTokens = +itd.%Get("cached_tokens")
                        }
                        If itd.%IsDefined("audio_tokens") {
                            Set inputAudioTokens = +itd.%Get("audio_tokens")
                        }
                    }
                }

                If usage.%IsDefined("output_tokens_details") {
                    Set otd = usage.%Get("output_tokens_details")
                    If $IsObject(otd) {
                        If otd.%IsDefined("audio_tokens") {
                            Set outputAudioTokens = +otd.%Get("audio_tokens")
                        }
                        If otd.%IsDefined("reasoning_tokens") {
                            Set outputReasoningTokens = +otd.%Get("reasoning_tokens")
                        }
                    }
                }
            } Catch ex {
                Set sc = $$$ERROR($$$GeneralError, "Failed to parse usage JSON")
            }
            If $$$ISERR(sc) {
                Quit sc
            }

            &sql(INSERT INTO Agents.Usage
            (usage_ts, chat_id, workflow, message_id, agent_name, production_name, model, reasoning_effort,
            input_tokens, output_tokens, total_tokens,
            input_cached_tokens, input_audio_tokens, output_audio_tokens,
            output_reasoning_tokens, duration_ms)
            VALUES
            (:ts, :pChatId, :pWorkflow, :pMessageId, :pAgentName, :pProductionName, :pModel, :pReasoningEffort,
            :inputTokens, :outputTokens, :totalTokens,
            :inputCachedTokens, :inputAudioTokens, :outputAudioTokens,
            :outputReasoningTokens, :durationMs))

            If SQLCODE < 0 {
                Set err = "Failed to insert Usage row. SQLCODE="_SQLCODE
                If $Get(%msg)'="" {
                    Set err = err_" MSG="_%msg
                }
                Quit $$$ERROR($$$GeneralError, err)
            }

            Quit $$$OK
        }

        ClassMethod ToText(pData As %RegisteredObject = "") As %String
        {
            Set out = ""

            If '$IsObject(pData) {
                Quit $Get(pData)
            }

            Set isStream = 0
            Set isDyn = 0

            Try {
                Set isStream = pData.%IsA("%Stream.Object")
            } Catch ex {
                Set isStream = 0
            }

            If isStream {
                Try {
                    Do pData.Rewind()
                    While 'pData.AtEnd {
                        Set out = out _ pData.Read(32000)
                    }
                } Catch ex {
                    Set out = ""
                }
                Quit out
            }

            Try {
                Set isDyn = pData.%IsA("%Library.DynamicAbstractObject")
            } Catch ex {
                Set isDyn = 0
            }

            If isDyn {
                Try {
                    Set out = pData.%ToJSON()
                } Catch ex {
                    Set out = ""
                }
                Quit out
            }

            Try {
                Set out = pData.%ToJSON()
            } Catch ex {
                Set out = ""
            }

            Quit out
        }

        ClassMethod ToJSONString(pValue As %RegisteredObject = "") As %String
        {
            Set out = ""

            If '$IsObject(pValue) {
                Quit $Get(pValue)
            }

            Set isStream = 0
            Set isDyn = 0

            Try {
                Set isStream = pValue.%IsA("%Stream.Object")
            } Catch ex {
                Set isStream = 0
            }

            If isStream {
                Set out = ..ToText(pValue)
                Quit out
            }

            Try {
                Set isDyn = pValue.%IsA("%Library.DynamicAbstractObject")
            } Catch ex {
                Set isDyn = 0
            }

            If isDyn {
                Try {
                    Set out = pValue.%ToJSON()
                } Catch ex {
                    Set out = ""
                }
                Quit out
            }

            Try {
                Set out = pValue.%ToJSON()
            } Catch ex {
                Set out = ""
            }

            Quit out
        }

        ClassMethod BuildLLMWrapperJSON(
            isTool As %Integer,
            toolkit As %String = "",
            tool As %String = "",
            content As %String = ""
        ) As %String
        {
            Set wrapped = "{""IsTool"":"_$Select(+$Get(isTool):"true",1:"false")
            Set wrapped = wrapped_",""Toolkit"":"""_$ZCONVERT($Get(toolkit),"O","JSON")_""""
            Set wrapped = wrapped_",""Tool"":"""_$ZCONVERT($Get(tool),"O","JSON")_""""
            Set wrapped = wrapped_",""Content"":"""_$ZCONVERT($Get(content),"O","JSON")_"""}"
            Quit wrapped
        }

        ClassMethod ToolResultToChat(toolkit As %String, tool As %String, result As %String) As %String
        {
            Set obj = ##class(%DynamicObject).%New()
            Do obj.%Set("type", "tool_result")
            Do obj.%Set("toolkit", ..ToJSONString(toolkit))
            Do obj.%Set("tool", ..ToJSONString(tool))
            Do obj.%Set("result", ..ToJSONString(result))
            Quit obj.%ToJSON()
        }

        ClassMethod BuildToolResultMessage(toolkit As %String, tool As %String, result As %String) As %DynamicObject
        {
            Set msg = ##class(%DynamicObject).%New()
            Do msg.%Set("role", "developer")
            Do msg.%Set("content", ..ToolResultToChat(toolkit, tool, result))
            Quit msg
        }

        ClassMethod AppendChat(
            chatId As %String,
            messageRole As %String,
            msg As %String,
            workflow As %String = ""
        ) As %Status
        {
            If $Get(chatId)="" Quit $$$OK

            &sql(INSERT INTO Agents.Chat (id, workflow, message_role, message)
                VALUES (:chatId, :workflow, :messageRole, :msg))

            If SQLCODE<0 Quit $$$ERROR($$$GeneralError,"Failed to append chat row")
            Quit $$$OK
        }

        ClassMethod ImportJSONToResponse(
            json As %String,
            responseClass As %String,
            Output response As %Library.Persistent
        ) As %Status
        {
            Set jsonText = ..ToJSONString(json)
            If jsonText="" {
                Quit $$$ERROR($$$GeneralError,"Empty JSON content for response import")
            }

            Set trimmed = $ZSTRIP(jsonText,"<>W")
            If $Extract(trimmed,1)'="{" {
                Quit $$$ERROR($$$GeneralError,"Response content must be a JSON object")
            }

            Try {
                Set response = $classmethod(responseClass, "%New")
                Set stream = ##class(%Stream.GlobalCharacter).%New()
                Do stream.Write(jsonText)
                Do stream.Rewind()
                Return response.%JSONImport(stream)
            } Catch ex {
                Return ex.AsStatus()
            }
        }

        ClassMethod LogToolUsage(
            pChatId As %String,
            pAgentName As %String,
            pToolkit As %String,
            pTool As %String,
            pRequestPayload As %String,
            pResponseOk As %Integer,
            pResponsePayload As %String,
            pWorkflow As %String = ""
        ) As %Status
        {
            Set ts = $ZDATETIME($HOROLOG,3,1,3)
            Set req = $Extract($Get(pRequestPayload),1,200000)
            Set resp = $Extract($Get(pResponsePayload),1,200000)

            &sql(INSERT INTO Agents.ToolUsage
                (usage_ts, chat_id, workflow, agent_name, toolkit, tool_name, request_payload, response_ok, response_payload)
                VALUES
                (:ts, :pChatId, :pWorkflow, :pAgentName, :pToolkit, :pTool, :req, :pResponseOk, :resp))

            If SQLCODE < 0 {
                Set err = "Failed to insert ToolUsage row. SQLCODE="_SQLCODE
                If $Get(%msg)'="" {
                    Set err = err_" MSG="_%msg
                }
                Quit $$$ERROR($$$GeneralError, err)
            }

            Quit $$$OK
        }

    }'''
    create_class('Agents.Utils.Common', cls_text)

def ensure_production_utils():

    ensure_schema('Chat')
    
    cls_text = f'''Class Agents.Utils.Production Extends %RegisteredObject
    {{
    
        ClassMethod ExtractOutputItems(respJson As %String) As %String
        {{
            Set outJson = ""
            Set outItems = ##class(%DynamicArray).%New()

            Try {{
                Set obj = ##class(%DynamicObject).%FromJSON(respJson)

                If obj.%IsDefined("output") {{
                    Set out = obj.%Get("output")
                    If $IsObject(out) {{
                        For i=0:1:out.%Size()-1 {{
                            Set item = out.%Get(i)

                            If '$IsObject(item) {{
                                Do outItems.%Push(item)
                                Continue
                            }}

                            Set itemJSON = item.%ToJSON()
                            Set itemCopy = ##class(%DynamicObject).%FromJSON(itemJSON)

                            Set itemType = ""
                            If itemCopy.%IsDefined("type") {{
                                Set itemType = itemCopy.%Get("type")
                            }}

                            If (itemType="reasoning") && itemCopy.%IsDefined("encrypted_content") {{
                                Do itemCopy.%Remove("encrypted_content")
                            }}

                            Do outItems.%Push(itemCopy)
                        }}
                    }}
                }}
                Set outJson = outItems.%ToJSON()
            }} Catch ex {{
                Set outJson = ""
            }}

            Quit outJson
        }}

        ClassMethod ExtractReasoningDetailed(respJson As %String) As %String
        {{
            Set outJson = ""
            Set outItems = ##class(%DynamicArray).%New()

            Try {{
                Set obj = ##class(%DynamicObject).%FromJSON(respJson)

                If obj.%IsDefined("output") {{
                    Set out = obj.%Get("output")
                    If $IsObject(out) {{
                        For i=0:1:out.%Size()-1 {{
                            Set item = out.%Get(i)
                            If '$IsObject(item) Continue
                            If item.%Get("type")'="reasoning" Continue
                            If 'item.%IsDefined("encrypted_content") Continue

                            Set itemJSON = item.%ToJSON()
                            Set itemCopy = ##class(%DynamicObject).%FromJSON(itemJSON)
                            Do outItems.%Push(itemCopy)
                        }}
                    }}
                }}

                Set outJson = outItems.%ToJSON()
            }} Catch ex {{
                Set outJson = ""
            }}

            Quit outJson
        }}

        ClassMethod StripReasoningItems(outputJson As %String) As %String
        {{
            Set outJson = ""
            Set stripped = ##class(%DynamicArray).%New()

            Try {{
                If $Get(outputJson)'="" {{
                    Set out = ##class(%DynamicArray).%FromJSON(outputJson)
                    For i=0:1:out.%Size()-1 {{
                        Set item = out.%Get(i)
                        If '$IsObject(item) {{
                            Do stripped.%Push(item)
                            Continue
                        }}

                        If item.%Get("type")="reasoning" Continue
                        Do stripped.%Push(item)
                    }}

                    Set outJson = stripped.%ToJSON()
                }}
            }} Catch ex {{
                Set outJson = ""
            }}

            Quit outJson
        }}

        ClassMethod MergeOutputWithReasoningDetailed(
            outputJson As %String = "",
            reasoningDetailedJson As %String = ""
        ) As %String
        {{
            Set outJson = ""

            Try {{
                Set merged = ##class(%DynamicArray).%New()

                If $Get(reasoningDetailedJson)'="" {{
                    Set reasoningItems = ##class(%DynamicArray).%FromJSON(reasoningDetailedJson)
                    For i=0:1:reasoningItems.%Size()-1 {{
                        Do merged.%Push(reasoningItems.%Get(i))
                    }}
                }}

                If $Get(outputJson)'="" {{
                    Set outputItems = ##class(%DynamicArray).%FromJSON(outputJson)
                    For i=0:1:outputItems.%Size()-1 {{
                        Do merged.%Push(outputItems.%Get(i))
                    }}
                }}

                Set outJson = merged.%ToJSON()
            }} Catch ex {{
                Set outJson = ""
            }}

            Quit outJson
        }}
    
        ClassMethod ExtractReasoningSummary(respJson As %String) As %String
        {{
            Set trace = ""
            Set sep = ""

            Try {{
                Set obj = ##class(%DynamicObject).%FromJSON(respJson)

                If obj.%IsDefined("reasoning") {{
                    Set topReasoning = obj.%Get("reasoning")
                    If $IsObject(topReasoning) {{
                        If topReasoning.%IsDefined("summary") {{
                            Set topSummary = topReasoning.%Get("summary")
                            If $IsObject(topSummary) {{
                                For i=0:1:topSummary.%Size()-1 {{
                                    Set part = topSummary.%Get(i)
                                    If '$IsObject(part) Continue
                                    If part.%IsDefined("text") {{
                                        Set text = part.%Get("text")
                                        If text'="" {{
                                            Set trace = trace _ sep _ text
                                            Set sep = $C(10)
                                        }}
                                    }}
                                }}
                            }}
                        }}
                    }}
                }}

                If obj.%IsDefined("output") {{
                    Set out = obj.%Get("output")

                    If $IsObject(out) {{
                        For i=0:1:out.%Size()-1 {{
                            Set item = out.%Get(i)
                            If '$IsObject(item) Continue
                            If item.%Get("type")'="reasoning" Continue

                            If item.%IsDefined("summary") {{
                                Set summary = item.%Get("summary")
                                If $IsObject(summary) {{
                                    For j=0:1:summary.%Size()-1 {{
                                        Set part = summary.%Get(j)
                                        If '$IsObject(part) Continue
                                        If part.%IsDefined("text") {{
                                            Set text = part.%Get("text")
                                            If text'="" {{
                                                Set trace = trace _ sep _ text
                                                Set sep = $C(10)
                                            }}
                                        }}
                                    }}
                                }}
                            }}

                            If item.%IsDefined("content") {{
                                Set content = item.%Get("content")
                                If $IsObject(content) {{
                                    For j=0:1:content.%Size()-1 {{
                                        Set part = content.%Get(j)
                                        If '$IsObject(part) Continue

                                        Set ptype = ""
                                        If part.%IsDefined("type") {{
                                            Set ptype = part.%Get("type")
                                        }}

                                        If (ptype="reasoning_text") || (ptype="") {{
                                            If part.%IsDefined("text") {{
                                                Set text = part.%Get("text")
                                                If text'="" {{
                                                    Set trace = trace _ sep _ text
                                                    Set sep = $C(10)
                                                }}
                                            }}
                                        }}
                                    }}
                                }}
                            }}
                        }}
                    }}
                }}
            }} Catch ex {{
                Set trace = ""
            }}

            Quit trace
        }}

        ClassMethod BuildNextLLMChatJSON(
            chatId As %String,
            userText As %String = "",
            assistantText As %String = "",
            toolkit As %String = "",
            tool As %String = "",
            toolResult As %String = "",
            systemPrompt As %String = "",
            toolManifest As %String = ""
        ) As %String
        {{
            Set arr = ##class(%DynamicArray).%New()

            If $Get(systemPrompt)'="" {{
                Set s1 = ##class(%DynamicObject).%New()
                Do s1.%Set("role","system")
                Do s1.%Set("content", systemPrompt)
                Do arr.%Push(s1)
            }}

            If $Get(toolManifest)'="" {{
                Set s2 = ##class(%DynamicObject).%New()
                Do s2.%Set("role","system")
                Do s2.%Set("content", toolManifest)
                Do arr.%Push(s2)
            }}

            If $Get(chatId)'="" {{
                &sql(DECLARE c CURSOR FOR
                    SELECT message_role, message
                    FROM Agents.Chat
                    WHERE id = :chatId
                    ORDER BY message_id)

                &sql(OPEN c)
                For {{
                    &sql(FETCH c INTO :dbRole, :dbMsg)
                    Quit:SQLCODE'=0

                    Set obj = ##class(%DynamicObject).%New()
                    Do obj.%Set("role", dbRole)
                    Do obj.%Set("content", dbMsg)
                    Do arr.%Push(obj)
                }}
                &sql(CLOSE c)
            }} Else {{
                If $Get(userText)'="" {{
                    Set u = ##class(%DynamicObject).%New()
                    Do u.%Set("role","user")
                    Do u.%Set("content", userText)
                    Do arr.%Push(u)
                }}

                If $Get(assistantText)'="" {{
                    Set a = ##class(%DynamicObject).%New()
                    Do a.%Set("role","assistant")
                    Do a.%Set("content", assistantText)
                    Do arr.%Push(a)
                }}

                If ($Get(toolkit)'="") && ($Get(tool)'="") {{
                    Do arr.%Push(##class(Agents.Utils.Common).BuildToolResultMessage(toolkit, tool, toolResult))
                }}
            }}

            Quit arr.%ToJSON()
        }}

        ClassMethod BuildChatJSON(
            chatId As %String,
            userText As %String = "",
            systemPrompt As %String = "",
            toolManifest As %String = ""
        ) As %String
        {{
            Set arr = ##class(%DynamicArray).%New()

            If $Get(systemPrompt)'="" {{
                Set s1 = ##class(%DynamicObject).%New()
                Do s1.%Set("role","system")
                Do s1.%Set("content", systemPrompt)
                Do arr.%Push(s1)
            }}

            If $Get(toolManifest)'="" {{
                Set s2 = ##class(%DynamicObject).%New()
                Do s2.%Set("role","system")
                Do s2.%Set("content", toolManifest)
                Do arr.%Push(s2)
            }}

            If $Get(chatId)'="" {{

                &sql(DECLARE c CURSOR FOR
                    SELECT message_role, message
                    FROM Agents.Chat
                    WHERE id = :chatId
                    ORDER BY message_id)

                &sql(OPEN c)
                For {{
                    &sql(FETCH c INTO :dbRole, :dbMsg)
                    Quit:SQLCODE'=0

                    Set obj = ##class(%DynamicObject).%New()
                    Do obj.%Set("role", dbRole)
                    Do obj.%Set("content", dbMsg)
                    Do arr.%Push(obj)
                }}
                &sql(CLOSE c)
            }}

            If $Get(userText)'="" {{
                Set u = ##class(%DynamicObject).%New()
                Do u.%Set("role","user")
                Do u.%Set("content", userText)
                Do arr.%Push(u)
            }}

            Quit arr.%ToJSON()
        }}

        ClassMethod BuildPropertySchema(pType As %String, pCollection As %String = "") As %DynamicObject
        {{
            Set t = $ZCONVERT($Get(pType),"U")
            Set c = $ZCONVERT($Get(pCollection),"U")
            Set s = ##class(%DynamicObject).%New()

            // collection of objects or scalars
            If c["LIST" {{
                Do s.%Set("type","array")

                Set itemSchema = ##class(%DynamicObject).%New()

                If ##class(%Dictionary.ClassDefinition).%ExistsId(pType) {{
                    Set itemSchema = ..BuildContentSchema(pType)
                }} ElseIf t["%BOOLEAN" {{
                    Do itemSchema.%Set("type","boolean")
                }} ElseIf (t["%INTEGER")!(t["%BIGINT") {{
                    Do itemSchema.%Set("type","integer")
                }} ElseIf (t["%NUMERIC")!(t["%DOUBLE")!(t["%FLOAT")!(t["%DECIMAL") {{
                    Do itemSchema.%Set("type","number")
                }} Else {{
                    Do itemSchema.%Set("type","string")
                }}

                Do s.%Set("items", itemSchema)
                Quit s
            }}

            // single embedded object
            If ##class(%Dictionary.ClassDefinition).%ExistsId(pType) {{
                Quit ..BuildContentSchema(pType)
            }}

            If t["%BOOLEAN" {{
                Do s.%Set("type","boolean")
                Quit s
            }}

            If (t["%INTEGER")!(t["%BIGINT") {{
                Do s.%Set("type","integer")
                Quit s
            }}

            If (t["%NUMERIC")!(t["%DOUBLE")!(t["%FLOAT")!(t["%DECIMAL") {{
                Do s.%Set("type","number")
                Quit s
            }}

            Do s.%Set("type","string")
            Quit s
        }}

        ClassMethod BuildContentSchema(pResponseType As %String) As %DynamicObject
        {{
            If '##class(%Dictionary.ClassDefinition).%ExistsId(pResponseType) {{
                Set s = ##class(%DynamicObject).%New()
                Do s.%Set("type","object")
                Set p = ##class(%DynamicObject).%New()
                Do p.%Set("text", ##class(%DynamicObject).%New().%Set("type","string"))
                Do s.%Set("properties", p)
                Set r = ##class(%DynamicArray).%New()
                Do r.%Push("text")
                Do s.%Set("required", r)
                Do s.%Set("additionalProperties", 0)
                Quit s
            }}

            Set cls = ##class(%Dictionary.ClassDefinition).%OpenId(pResponseType)
            If cls="" {{
                Set s = ##class(%DynamicObject).%New()
                Do s.%Set("type","object")
                Set p = ##class(%DynamicObject).%New()
                Do p.%Set("text", ##class(%DynamicObject).%New().%Set("type","string"))
                Do s.%Set("properties", p)
                Set r = ##class(%DynamicArray).%New()
                Do r.%Push("text")
                Do s.%Set("required", r)
                Do s.%Set("additionalProperties", 0)
                Quit s
            }}

            Set schema = ##class(%DynamicObject).%New()
            Do schema.%Set("type","object")

            Set props = ##class(%DynamicObject).%New()
            Set req = ##class(%DynamicArray).%New()

            Set propCount = cls.Properties.Count()
            For i=1:1:propCount {{
                Set prop = cls.Properties.GetAt(i)
                If prop="" Continue
                If prop.TransientGet()=1 Continue
                If prop.CalculatedGet()=1 Continue
                If prop.PrivateGet()=1 Continue

                Set pname = prop.NameGet()
                If pname="" Continue

                Set jsonName = prop.Parameters.GetAt("%JSONFIELDNAME")
                If jsonName="" Set jsonName = pname

                Set pCollection = ""
                Try {{
                    Set pCollection = prop.CollectionGet()
                }} Catch ex {{
                    Set pCollection = ""
                }}

                Set ps = ..BuildPropertySchema(prop.TypeGet(), pCollection)
                Do props.%Set(jsonName, ps)
                Do req.%Push(jsonName)
            }}

            If req.%Size()=0 {{
                Do props.%Set("text", ##class(%DynamicObject).%New().%Set("type","string"))
                Do req.%Push("text")
            }}

            Do schema.%Set("properties", props)
            Do schema.%Set("required", req)
            Do schema.%Set("additionalProperties", 0)

            Quit schema
        }}

        ClassMethod BuildLLMOutputSchema() As %DynamicObject
        {{
            Set schema = ##class(%DynamicObject).%New()
            Do schema.%Set("type","object")

            Set props = ##class(%DynamicObject).%New()
            Do props.%Set("IsTool", ##class(%DynamicObject).%New().%Set("type","boolean"))
            Do props.%Set("Toolkit", ##class(%DynamicObject).%New().%Set("type","string"))
            Do props.%Set("Tool", ##class(%DynamicObject).%New().%Set("type","string"))
            Do props.%Set("Content", ##class(%DynamicObject).%New().%Set("type","string"))

            Set req = ##class(%DynamicArray).%New()
            Do req.%Push("IsTool")
            Do req.%Push("Toolkit")
            Do req.%Push("Tool")
            Do req.%Push("Content")

            Do schema.%Set("properties", props)
            Do schema.%Set("required", req)
            Do schema.%Set("additionalProperties", 0)

            Quit schema
        }}

        ClassMethod ExtractOutputText(respJson As %String) As %String
        {{
            Set outText = ""

            Try {{
                Set obj = ##class(%DynamicObject).%FromJSON(respJson)
                Set out = obj.%Get("output")
                If out="" Return ""

                For i=0:1:out.%Size()-1 {{
                    Set item = out.%Get(i)
                    If item.%Get("type")'="message" Continue

                    Set content = item.%Get("content")
                    If content="" Continue

                    Set messageText = ""

                    For j=0:1:content.%Size()-1 {{
                        Set part = content.%Get(j)
                        If part.%Get("type")="output_text" {{
                            Set messageText = messageText _ part.%Get("text")
                        }}
                    }}

                    If messageText'="" {{
                        Set outText = messageText
                    }}
                }}
            }} Catch ex {{
                Return ""
            }}

            Return outText
        }}
    }}
    '''
    create_class('Agents.Utils.Production', cls_text)
