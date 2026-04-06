from pydantic import BaseModel
from enum import Enum

class MessageType(Enum):
    REQ = 'Request'
    RES = 'Response'

class LLMOutput(BaseModel):
    is_tool: bool
    toolkit: str
    tool: str
    content: str

class LLMRequest(BaseModel):
    model: str
    responseType: str
    chat: str

class LLMResponse(BaseModel):
    is_tool: bool
    toolkit: str
    tool: str
    content: str
    usage: str

class ToolRequest(BaseModel):
    id: str
    toolkit: str
    name: str
    params: str

class ToolResponse(BaseModel):
    id: str
    toolkit: str
    ok: bool
    result: str

class Request(BaseModel):
    chatId: str = "default"
    message: str
    responseType: str = "Agents.Message.Response"

class Response(BaseModel):
    chatId: str = ""
    message: str