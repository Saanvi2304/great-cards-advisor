
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def load_env(file_path: str = ".env") -> None:
    # This script lives in scripts/, so .env is in the parent (project root) folder.
    env_path = Path(__file__).resolve().parent.parent / file_path
    if env_path.is_file():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


load_env()

MCP_URL = os.getenv("MCP_URL")
if not MCP_URL:
    raise ValueError("MCP_URL environment variable is not set. Please define it in .env")


async def main():
    print("Connecting to Great.cards MCP server...")

    async with streamable_http_client(MCP_URL) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            print("Connected successfully!\n")

            result = await session.list_tools()

            print(f"Found {len(result.tools)} tools.\n")

            for tool in result.tools:
                print("=" * 70)
                print(f"Name: {tool.name}")
                print(f"Description: {tool.description or 'No description provided'}")
                print("Input schema:")
                print(json.dumps(tool.input_schema, indent=2))

    print("\nInspection complete.")


if __name__ == "__main__":
    asyncio.run(main())
