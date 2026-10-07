"""Add a red test cube to verify the Blender MCP connection.

This smoke check is additive: it leaves existing scene objects untouched.
"""

import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SERVER = StdioServerParameters(command="uvx", args=["mcp-for-blender"])


async def main() -> None:
    print("Starting Blender MCP server...")
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            print("Initializing MCP connection...")
            await session.initialize()
            print("Connected!")
            result = await session.call_tool(
                "execute_blender_code",
                arguments={
                    "code": """
import bpy

bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
cube = bpy.context.active_object
cube.name = "MCP_TestCube"
material = bpy.data.materials.new("MCP_TestCube_Red")
material.diffuse_color = (1.0, 0.0, 0.0, 1.0)
cube.data.materials.append(material)
"""
                },
            )
            print("\nBlender response:")
            print(result)


if __name__ == "__main__":
    asyncio.run(main())
