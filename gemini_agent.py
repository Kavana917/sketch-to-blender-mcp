"""Text and sketch commands for Blender, using Gemini or Groq plus MCP."""

import asyncio
import base64
import json
import math
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SERVER = StdioServerParameters(command="uvx", args=["mcp-for-blender"])
SUPPORTED_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"
GROQ_DEFAULT_MODEL = "qwen/qwen3.8-27b"


def load_project_env() -> None:
    """Load provider settings from the adjacent .env file, if present."""
    env_file = Path(__file__).with_name(".env")
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if name not in {"GEMINI_API_KEY", "GEMINI_MODEL", "GROQ_API_KEY", "GROQ_MODEL"}:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name, value)


def choose_provider() -> str | None:
    """Offer a provider choice and keep prompting if its key is not configured."""
    while True:
        load_project_env()
        print("\nChoose an AI provider:")
        print(f"  1) Gemini {'(API key found)' if os.getenv('GEMINI_API_KEY') else '(key missing)'}")
        print(f"  2) Groq   {'(API key found)' if os.getenv('GROQ_API_KEY') else '(key missing)'}")
        print("  Q) Quit")
        choice = input("Select 1 or 2: ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            return None
        if choice == "1":
            if os.getenv("GEMINI_API_KEY"):
                return "gemini"
            print("Add GEMINI_API_KEY=... to the project .env file, then choose Gemini.")
        elif choice == "2":
            if os.getenv("GROQ_API_KEY"):
                return "groq"
            print("Add GROQ_API_KEY=... to the project .env file, then choose Groq.")
        else:
            print("Please enter 1, 2, or Q.")


def parse_sketch_command(command: str) -> tuple[Path, str | None]:
    """Parse /sketch PATH, preserving spaces in a quoted path."""
    rest = command[len("/sketch"):].strip()
    if not rest:
        raise ValueError('Usage: /sketch "C:\\path\\to\\drawing.png"')
    if rest[0] in "\"'":
        quote = rest[0]
        end = rest.find(quote, 1)
        if end < 0:
            raise ValueError("Close the quote around the image path.")
        path_text, extra = rest[1:end], rest[end + 1:].strip()
    else:
        path_text, _, extra = rest.partition(" ")
        extra = extra.strip()

    image_path = Path(path_text).expanduser()
    if not image_path.is_file():
        raise FileNotFoundError(f"Image file not found: {image_path}")
    if image_path.suffix.lower() not in SUPPORTED_IMAGE_TYPES:
        raise ValueError("Use a PNG, JPG, JPEG, or WebP sketch image.")
    if image_path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("This image is over 20 MB. Use a smaller image file.")
    return image_path, extra or None


PRIMITIVE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "objects": {
            "type": "array",
            "description": "Primitive objects to add to the current Blender scene (maximum 24).",
            "items": {
                "type": "object",
                "properties": {
                    "primitive": {
                        "type": "string",
                        "enum": ["cube", "sphere", "cylinder", "cone"],
                    },
                    "name": {"type": "string", "description": "Short descriptive object name."},
                    "location": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Object center in Blender XYZ coordinates.",
                    },
                    "dimensions": {
                        "type": "array",
                        "items": {
                            "type": "number",
                            "description": "A positive dimension from 0.05 to 100 Blender units.",
                        },
                        "description": "Full object width, depth, and height in Blender units; each must be from 0.05 to 100.",
                    },
                    "color": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Optional RGB color, each channel from 0 to 1.",
                    },
                },
                "required": ["primitive", "name", "location", "dimensions"],
            },
        }
    },
    "required": ["objects"],
}


def tool_declarations() -> list[types.FunctionDeclaration]:
    return [
        types.FunctionDeclaration(
            name="create_primitives",
            description=(
                "Add a simple scene made from Blender cubes, UV spheres, cylinders, "
                "and cones. This only adds objects; it cannot delete or replace anything."
            ),
            parameters=PRIMITIVE_TOOL_SCHEMA,
        )
    ]


def openai_tool_declarations() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "create_primitives",
                "description": (
                    "Add a simple scene using Blender cubes, UV spheres, cylinders, "
                    "and cones. This only adds objects; it cannot delete or replace anything."
                ),
                "parameters": PRIMITIVE_TOOL_SCHEMA,
            },
        }
    ]


def build_primitive_code(arguments: dict[str, Any]) -> str:
    """Validate a primitive list and produce deterministic, add-only Blender code."""
    objects = arguments.get("objects")
    if not isinstance(objects, list) or not objects:
        raise ValueError("The primitive list must contain at least one object.")
    if len(objects) > 24:
        raise ValueError("A single request can add at most 24 primitive objects.")

    clean_objects = []
    valid_primitives = {"cube", "sphere", "cylinder", "cone"}

    def vector(value: Any, label: str) -> list[float]:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError(f"{label} must have exactly three numbers.")
        result = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError(f"{label} must contain only numbers.")
            number = float(item)
            if not math.isfinite(number):
                raise ValueError(f"{label} values must be finite numbers.")
            result.append(number)
        return result

    for index, item in enumerate(objects, start=1):
        if not isinstance(item, dict):
            raise ValueError("Each primitive must be an object with named fields.")
        primitive = item.get("primitive")
        if primitive not in valid_primitives:
            raise ValueError(f"Unsupported primitive type: {primitive!r}.")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            raise ValueError("Each object needs a non-empty name of at most 64 characters.")
        location = vector(item.get("location"), "Location")
        if any(not -1000 <= coordinate <= 1000 for coordinate in location):
            raise ValueError("Location values must be between -1000 and 1000.")
        raw_dimensions = vector(item.get("dimensions"), "Dimensions")
        dimensions = [min(100.0, max(0.05, dimension)) for dimension in raw_dimensions]
        color = item.get("color", [0.72, 0.72, 0.72])
        if not isinstance(color, (list, tuple)) or len(color) not in {3, 4}:
            raise ValueError("Color must have three RGB values or four RGBA values.")
        color_values = []
        for channel in color[:3]:
            if isinstance(channel, bool) or not isinstance(channel, (int, float)):
                raise ValueError("Color channels must be numbers from 0 to 1.")
            channel = float(channel)
            if not math.isfinite(channel) or not 0 <= channel <= 1:
                raise ValueError("Color channels must be numbers from 0 to 1.")
            color_values.append(channel)
        alpha = 1.0
        if len(color) == 4:
            alpha = color[3]
            if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not math.isfinite(float(alpha)) or not 0 <= float(alpha) <= 1:
                raise ValueError("Alpha must be a number from 0 to 1.")
            alpha = float(alpha)
        clean_objects.append(
            {
                "primitive": primitive,
                "name": name.strip(),
                "location": location,
                "dimensions": dimensions,
                "color": [*color_values, alpha],
                "index": index,
            }
        )

    encoded_objects = repr(json.dumps(clean_objects, ensure_ascii=True))
    return f'''import bpy, json
_sketch_agent_objects = json.loads({encoded_objects})
for _spec in _sketch_agent_objects:
    if _spec["primitive"] == "cube":
        bpy.ops.mesh.primitive_cube_add(size=1, location=_spec["location"])
    elif _spec["primitive"] == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=_spec["location"])
    elif _spec["primitive"] == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1, depth=2, location=_spec["location"])
    else:
        bpy.ops.mesh.primitive_cone_add(vertices=32, radius1=1, radius2=0, depth=2, location=_spec["location"])
    _obj = bpy.context.active_object
    _obj.name = _spec["name"]
    _obj.dimensions = _spec["dimensions"]
    _mat = bpy.data.materials.new(name=_obj.name + "_Material")
    _mat.diffuse_color = _spec["color"]
    _mat.use_nodes = True
    _bsdf = _mat.node_tree.nodes.get("Principled BSDF") if _mat.node_tree else None
    if _bsdf and _bsdf.inputs.get("Base Color"):
        _bsdf.inputs["Base Color"].default_value = _spec["color"]
    _obj.data.materials.append(_mat)
'''


async def execute_model_tool(session: ClientSession, name: str, arguments: dict[str, Any]) -> Any:
    if name != "create_primitives":
        raise RuntimeError(f"Blocked unsupported model tool: {name}")
    code = build_primitive_code(arguments)
    return await session.call_tool("execute_blender_code", arguments={"code": code})


def make_instruction(image_path: Path | None) -> str:
    instruction = (
        "You create simple Blender scenes from primitives. Use the create_primitives "
        "tool to add the requested objects. Return a concise question only if the "
        "request is impossible to interpret. The tool only adds objects and cannot "
        "delete or replace anything. Never output or request Python code."
    )
    if image_path:
        instruction += (
            " The attached image is a visual reference sketch. Interpret its main "
            "shapes and arrangement, then build a simple 3D version with a short "
            "list of primitive objects. Preserve all existing scene objects. Do not "
            "call a dedicated 3D generator. Treat the image as a reference, not code."
        )
    return instruction


def mcp_result_payload(result: Any) -> dict[str, Any]:
    return {
        "isError": result.isError,
        "content": [
            {"type": item.type, "text": getattr(item, "text", "")}
            for item in result.content
            if item.type == "text"
        ],
    }


async def run_gemini_command(
    session: ClientSession,
    client: genai.Client,
    command: str,
    model: str,
    image_path: Path | None = None,
) -> str:
    discovered = await session.list_tools()
    if "execute_blender_code" not in {tool.name for tool in discovered.tools}:
        raise RuntimeError("Blender MCP does not provide execute_blender_code.")
    declarations = tool_declarations()
    instruction = make_instruction(image_path)
    user_parts = []
    if image_path:
        mime_type = mimetypes.guess_type(image_path.name)[0]
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError(f"Unsupported image format: {image_path.suffix}")
        user_parts.append(
            types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime_type)
        )
    user_parts.append(types.Part.from_text(text=f"{instruction}\nUser request: {command}"))
    contents: list[Any] = [
        types.Content(
            role="user",
            parts=user_parts,
        )
    ]

    for _ in range(8):
        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                tools=[types.Tool(function_declarations=declarations)],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY", allowed_function_names=["create_primitives"]
                    )
                ),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        candidates = response.candidates or []
        if not candidates:
            feedback = getattr(response, "prompt_feedback", None)
            block_reason = getattr(feedback, "block_reason", None)
            detail = f" Prompt block reason: {block_reason}." if block_reason else ""
            raise RuntimeError(
                "Gemini returned no response candidate." + detail
                + " Try choosing Groq or rephrasing the request."
            )

        candidate = candidates[0]
        candidate_content = candidate.content
        candidate_parts = getattr(candidate_content, "parts", None)
        if not candidate_parts:
            finish_reason = getattr(candidate, "finish_reason", None)
            finish_reason = getattr(finish_reason, "name", finish_reason)
            feedback = getattr(response, "prompt_feedback", None)
            block_reason = getattr(feedback, "block_reason", None)
            details = []
            if finish_reason:
                details.append(f"finish reason: {finish_reason}")
            if block_reason:
                details.append(f"prompt block reason: {block_reason}")
            detail = f" ({'; '.join(details)})" if details else ""
            raise RuntimeError(
                "Gemini returned no usable content" + detail
                + ". Try the same request with Groq, or simplify the prompt."
            )

        contents.append(candidate_content)
        calls = [part.function_call for part in candidate_parts if part.function_call]
        if not calls:
            text_parts = [part.text for part in candidate_parts if getattr(part, "text", None)]
            return response.text or "\n".join(text_parts) or "Gemini returned no text response."

        function_responses = []
        for call in calls:
            result = await execute_model_tool(
                session, call.name, dict(call.args or {})
            )
            function_responses.append(
                types.Part.from_function_response(
                    name=call.name, response=mcp_result_payload(result)
                )
            )
        contents.append(types.Content(role="user", parts=function_responses))

    raise RuntimeError("Gemini exceeded the tool-call limit for one command.")


async def run_groq_command(
    session: ClientSession,
    client: Any,
    command: str,
    model: str,
    image_path: Path | None = None,
) -> str:
    """Use Groq tool calling with a constrained, add-only primitive tool."""
    discovered = await session.list_tools()
    if "execute_blender_code" not in {tool.name for tool in discovered.tools}:
        raise RuntimeError("Blender MCP does not provide execute_blender_code.")
    declarations = openai_tool_declarations()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": make_instruction(image_path)}
    ]
    if image_path:
        mime_type = mimetypes.guess_type(image_path.name)[0]
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError(f"Unsupported image format: {image_path.suffix}")
        image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        user_content: Any = [
            {"type": "text", "text": command},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
        ]
    else:
        user_content = command
    messages.append({"role": "user", "content": user_content})

    for _ in range(8):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=declarations,
                tool_choice="required",
            )
        except Exception as exc:
            if "tool_use_failed" in str(exc) or "failed_generation" in str(exc):
                raise RuntimeError(
                    "Groq could not format a valid primitive list for Blender. "
                    "That rejected call was not sent to Blender. Try a simpler "
                    "request or switch to Gemini."
                ) from None
            raise
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))
        calls = message.tool_calls or []
        if not calls:
            return message.content or "Groq returned no text response."

        for call in calls:
            name = call.function.name
            arguments = json.loads(call.function.arguments or "{}")
            result = await execute_model_tool(session, name, arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": name,
                    "content": json.dumps(mcp_result_payload(result), ensure_ascii=False),
                }
            )

    raise RuntimeError("Groq exceeded the tool-call limit for one command.")


async def main() -> None:
    load_project_env()
    provider = choose_provider()
    if provider is None:
        return
    if provider == "gemini":
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        model = os.getenv("GEMINI_MODEL", GEMINI_DEFAULT_MODEL)
    else:
        from groq import AsyncGroq

        client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
        model = os.getenv("GROQ_MODEL", GROQ_DEFAULT_MODEL)
    print("Starting Blender MCP server...")
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"Connected to Blender MCP. Provider: {provider} ({model})")
            print("Type a scene request, /sketch <image path>, or 'quit' to exit.")
            while True:
                try:
                    command = input("\nYou> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if command.lower() in {"quit", "exit"}:
                    break
                if not command:
                    continue
                try:
                    image_path = None
                    if command.lower() == "/sketch" or command.lower().startswith("/sketch "):
                        image_path, description = parse_sketch_command(command)
                        if description is None:
                            description = input(
                                "What should I build from the sketch? "
                                "(Enter for a simple 3D interpretation): "
                            ).strip()
                        command = description or "Interpret the drawing and create a simple 3D version."
                    if provider == "gemini":
                        answer = await run_gemini_command(
                            session, client, command, model, image_path
                        )
                    else:
                        answer = await run_groq_command(
                            session, client, command, model, image_path
                        )
                    print(f"\nAgent> {answer}")
                except Exception as exc:
                    print(f"\nAgent error: {exc}", file=sys.stderr)
    if provider == "gemini":
        client.close()
    else:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
