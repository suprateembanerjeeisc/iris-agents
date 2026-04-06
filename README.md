# IRIS Agents

A Python framework for building and orchestrating AI agents on InterSystems IRIS.

## Overview

This project provides an effective way to:

- Define Agents with specific tool access, structured outputs, and models, all using Python
- Persist conversations, prompts, tools, telemetry, and agent metadata in IRIS
- Connect MCP servers as Toolkits
- Trace agentic messages via InterSystems IRIS Management Portal
- Version control Prompts to effectively run A/B tests

## Project Structure

```text
.
├── agents/
│   ├── __init__.py
│   ├── Agent.py
│   ├── Chat.py
│   ├── Message.py
│   ├── models.py
│   ├── Production.py
│   ├── Prompt.py
│   ├── Toolkit.py
│   └── utils.py
├── .env
├── demo.ipynb
├── server.py
└── requirements.txt
```
### Features

- Agent message tracing on IRIS
- MCP Servers added as Toolkits
- Prompt versioning and testing
- Structured outputs or plain-text responses
- Chat persistence in IRIS
- Integration with InterSystems IRIS productions

### Requirements
- Python 3.12+
- InterSystems IRIS 2025.1+

### Installation
- Clone the repository
  ```
  git clone https://github.com/suprateembanerjeeisc/iris-agents.git
  ```
- Create a virtual environment and install dependencies
  ```bash
  conda create -n irisagents python=3.12
  conda activate irisagents
  pip install -r requirements.txt
  ```
- Create a `.env` file similar to this (Note the port is the superserver port)
  ```
  OPENAI_API_KEY="sk..."
  IRIS_HOSTNAME = '127.0.0.1'
  IRIS_PORT = 1972
  IRIS_USERNAME = 'SuperUser'
  IRIS_PASSWORD = 'SYS'
  ```
- Modify `server.py` and `iris_mcp.py` to configure port if needed, then run both servers
  ```bash
  python server.py
  python iris_mcp.py
  ```
- Run demo.ipynb
  
  **Note: Make sure to initialize Toolkit object using the correct port as specified above**

## API
### Chat
The Chat API is used to persist conversations. Using the name as an identifier, the framework constructs past conversations with a configurable limit before sending it to LLM. User and assistant messages are automatically persisted in IRIS. When an agent is operated without the `chat` parameter, it is stateless by default. Tool calls are logged in a separate table and not included in chat history.
```python
context = Chat(
    name='travel',
    messages=[
        {'role': 'system', 'content': 'You are helpful.'},
        {'role': 'user', 'content': 'We are in Washington DC'},
        {'role': 'assistant', 'content': 'Great, what do you want to do in DC?'}
    ]
)
```
This can be used as
```python
molly(message='What is the weather today?', chat='travel')
```
or 
```python
molly(message='What is the weather today?', chat=context)
```
or
```python
molly(message='What is the weather today?', chat=Chat('travel'))
```

### Prompt
The Prompt API is used to store prompts in IRIS. A prompt can be retrieved by its name, and subsequent changes to a prompt are versioned appropriately. This enables experimentation and A/B tests across versions of prompts. Prompts can also be built using variables that fill in placeholders in the Prompt.
```python
bond_system = Prompt(name = 'Agent007', text = 'You are {agent_name}. You always stay in character.')
bond_system.build(agent_name='James Bond')
```
which results in
```text
'You are James Bond. You always stay in character.'
```
Now if we update it,
```python
bond_system = Prompt(name = 'Agent007',
                text = 'Your next mission is of utmost importance, you do not have time to talk.')
```
we get the next version, while the previous version is persisted and still can be built
```python
Prompt(name='Agent007', version=2, text='Your next mission is of utmost importance, you do not have time to talk.')
```
Re-initialization of an older prompt prevents duplicate additions, and returns an instance of the previous object.

### Toolkit
The Toolkit API is used to manage Model Context Protocol (MCP) tools that Agents might have access to. When the Production is built, the MCP server is auto-initialized and the Session ID stored in IRIS Credentials, alongside LLM API Key. A demonstrative server serving two tools can be run using the `server.py` in this repository.
```python
utils_toolkit = Toolkit(name = 'Utilities', url = 'http://localhost:8000/mcp')
```

### Agent

The Agent, its specification, its messages and its Tool Calling behavior is persisted across several tables in IRIS. An Agent can be configured with a set of tools, a system prompt, a model, and a default structured output schema.
```python
class MollyResponse(BaseModel):
    text: str
    reasoning: str

molly = Agent(name='Molly', 
             description='Molly is the first agent ever created on IRIS Agents', 
             system_prompt=Prompt(name='molly_system', text='You are a helpful agent'),
             model='gpt-5',
             toolkits=[utils_toolkit],
             response_format=MollyResponse)
```
Agents can be operated once they have been added to a Production (see below) in stateful or stateless ways
```python
molly(message='Recommend some good food spots for lunch', chat='travel') # Stateful
molly(message='Recommend some good food spots for lunch')                # Stateless
```
Agents can also be queried for a specified response format at runtime that supersedes their default response format
```python
class Restaurant(BaseModel):
    name: str
    cuisine: str

class TasteAtlas(BaseModel):
    restaurants: list[Restaurant]
    reasoning: str

molly(message='What are some places I would like? I tend to like Italian and Asian cuisines',
      response_format=TasteAtlas, chat='travel')
```
### Production
Agents must be added to a Production before they can be queried. The Production API stops any running production in the Agents Namespace and starts the specified Production. Agents are implemented as Business Processes, while LLM and Toolkits are implemented as Business Operations. The Production adds all Toolkits among all added agents, even if all Agents may not have access to all Toolkits. 

For every Agent, a corresponding Business Service is created as a Gateway. Once the Production has been created, it needs to be started using `start()`. After that point, agents can be independently called, and as long as they are part of the running Production, they will behave independently.
```python
Production('AgentSpace', [molly, alex]).start()
```
### Observability
Token usage is logged at every LLM and Tool call, and can be tracked at various levels. Production, Agent, and Chat all contain a usage() method, which returns aggregated values per Production, per Agent and per Chat respectively.
```python
Production('AgentSpace').usage()
Agent('Molly').usage()
Chat('travel').usage()
```
A Production usage can be further predicated with `agent_name` and `model` fields.
```python
Production('AgentSpace').usage(agents=[Agent('Molly'), Agent('Alex')])
Production('AgentSpace').usage(model='gpt-5')
```
The output of all usage() calls share the same structure:
```json
{'input_tokens': 19136,
 'output_tokens': 35992,
 'output_reasoning_tokens': 28416,
 'total_tokens': 55128}
```
### Messages
Though the Messages structure is not meant to be used externally, this is used to create representations of Pydantic BaseModels as Objectscript classes. Internally, structured outputs are facilitated by Messages.
```python
class Restaurant(BaseModel):
    name: str
    cuisine: str

class TasteAtlas(BaseModel):
    restaurants: list[Restaurant]
    reasoning: str

Message(name = 'TasteAtlas', 
        model = TasteAtlas, 
        message_type = 'Response')
```

## Why I built this

This kind of a framework bridges the gap between IRIS as a powerful multimodel data platform and a AI Engineer who wants to code purely in Python and build Agents on top of a framework that makes their workflow faster. Alternative agentic frameworks like Strands and Autogen often add significant latency overhead over directly calling an LLM (In my limited experimentation, I found 3X latency increase when using Strands compared to OpenAI Responses). 
IRIS Agents, on the other hand, adds virtually no latency to the LLM call.

Moreover, this enables IRIS to be the singular data platform for building AI Applications. While data can flow in from a multitude of sources, Agents can have access to the most real time information, while allowing the engineer to observe the workflow and run experiments with context engineering, prompt tuning, and explore alternative orchestration architectures, all on a singular platform.


