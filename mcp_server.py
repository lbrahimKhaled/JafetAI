from dotenv import load_dotenv

load_dotenv()

# stdout is the JSON-RPC channel - litellm must not print on it
import litellm

litellm.suppress_debug_info = True
import logging

logging.getLogger("LiteLLM").setLevel(logging.WARNING)

from mcp.server.mcpserver import MCPServer

from jafet import service

mcp = MCPServer("jafet")


@mcp.tool()
async def chat(session_id: str, message: str) -> str:
    """Talk to JafetAI, the AUB library seat reservation bot.
    Use the same session_id to continue a conversation."""
    return await service.run_turn(session_id, message, user_id="mcp")


if __name__ == "__main__":
    mcp.run()
