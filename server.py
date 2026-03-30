from typing import Dict
import uuid
from fastmcp import FastMCP

# json_response=True ensures tool results are returned as JSON payloads
mcp = FastMCP("Utility Server")


@mcp.tool()
def weather(city:str) -> Dict[str, str]:
    """Return today's weather report."""
    return {"city": city, "high": "26", "low": "13", "sky": "Cloudy"}


@mcp.tool()
def calendar() -> Dict[str, str]:
    """Return today's schedule."""
    return {
        "type": "meeting",
        "subject": "Quarterly Financial Review",
        "participants": "John Doe",
        "start_time": "10:30AM",
        "end_time": "12:00PM",
    }


if __name__ == "__main__":
    # Exposes Streamable HTTP at http://localhost:8000/mcp by default
    # mcp.run(transport="streamable-http")
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=9001,
        path="/mcp",
        json_response=True
    )