# Sketch to Blender Agent

A small local Python agent that turns text instructions or a sketch image into
simple Blender scenes. Choose Gemini or Groq at startup. The selected model
interprets the request and returns structured object descriptions; Python
validates those descriptions and sends an add-only Blender command through MCP.

> This is a learning project and blockout generator. It creates editable Blender
> primitives; it does not produce detailed sculpted meshes.

## Features

- Choose Gemini or Groq each time the agent starts.
- Create or revise a scene from a text command.
- Interpret a PNG, JPG/JPEG, or WebP sketch up to 20 MB.
- Build scenes from cubes, UV spheres, cylinders, and cones.
- Assign basic material colors.
- Add new objects without deleting or replacing existing scene objects.
- Validate primitive names, positions, dimensions, colors, and object count
  before Blender receives a command.

The agent uses the same safe primitive tool for both providers. It does not
expose free-form Python to the model and does not depend on Blender MCP's
Hunyuan3D or Hyper3D Rodin services.

## How it works

```text
Text command or sketch image
            |
            v
      Gemini or Groq
            |
            | structured primitive list
            v
       Python agent
       - validates values
       - builds add-only Blender code
            |
            | MCP execute_blender_code
            v
          Blender
```

Gemini and Groq each receive the relevant tool declaration. For a sketch, the
image is sent to the provider selected for that run. The agent does not save or
upload the image anywhere else.

## Requirements

- Windows, macOS, or Linux
- Python 3.10 or newer
- Blender with the MCP for Blender add-on installed and running
- `uvx` available on `PATH` (provided by Astral `uv`)
- A Gemini API key, a Groq API key, or both

The MCP server is started by the agent with `uvx mcp-for-blender`. Blender must
already be open with its add-on connected. The default local port is 9876.
See the [MCP for Blender project](https://github.com/ahujasid/mcp-for-blender)
for add-on installation instructions.

## Installation

Open PowerShell in the cloned project folder and create a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-sketch-agent.txt
```

If PowerShell blocks the activation script, call the environment's Python
directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-sketch-agent.txt
```

If `uvx` is not recognized, install `uv` and open a new terminal:

```powershell
python -m pip install uv
```

## API keys

Copy the example environment file, then edit `.env` and add the key for each
provider you want to use:

```powershell
Copy-Item .env.example .env
notepad .env
```

The file uses these variable names:

```text
GEMINI_API_KEY=your_gemini_key
GROQ_API_KEY=your_groq_key
GEMINI_MODEL=gemini-2.5-flash
GROQ_MODEL=qwen/qwen3.8-27b
```

Leave out a provider key if you do not plan to use that provider. Never commit
`.env` or paste API keys into source code. `.env` is excluded by `.gitignore`.

## Run

1. Open Blender and connect the MCP for Blender add-on.
2. Open PowerShell in this project folder and activate `.venv` as shown above.
3. Start the agent:

   ```powershell
   python gemini_agent.py
   ```

4. Select `1` for Gemini or `2` for Groq.
5. Enter a text scene request or a sketch command. Type `quit` to stop.

Text example:

```text
Create a blue sphere beside the cube. Keep the existing objects.
```

Sketch example (quote paths that contain spaces):

```text
/sketch "C:\Users\You\Pictures\house-sketch.jpg" Build a simple blockout from cubes, cylinders, and cones. Keep existing objects.
```

The sketch path can be followed by instructions on the same line. If you omit
the instructions, the agent asks what you want built.

## Files

| File | Purpose |
| --- | --- |
| `gemini_agent.py` | Provider menu, image/text input, tool-call loop, validation, and Blender MCP bridge. |
| `test_blender_mcp.py` | Small additive MCP connection smoke check that creates a red cube. |
| `requirements-sketch-agent.txt` | Python package dependencies. |
| `.env.example` | Safe template for local API-key and model settings. |
| `.gitignore` | Excludes secrets, virtual environments, caches, and Blender project files. |

## Troubleshooting

### A provider is marked “key missing”

Check that `.env` is in the same folder as `gemini_agent.py`, and that the
setting is spelled exactly `GEMINI_API_KEY` or `GROQ_API_KEY`. Restart the agent
after editing `.env`.

### Blender MCP is not connected

Open Blender, enable the add-on, and connect its MCP server. Then restart the
Python agent. Check that port 9876 is available if the connection still fails.

### A Groq tool call is rejected

The agent uses a short structured primitive list rather than asking the model to
write Blender Python. Try simplifying a very complex sketch request or choose
Gemini from the startup menu.

### Objects look gray in the viewport

Switch Blender's viewport shading to **Material Preview** to see material
colors.

## Safety and limitations

- The model can only request cubes, spheres, cylinders, and cones through the
  agent's exposed tool. It cannot use that tool to delete existing objects.
- Generated scenes are rough, editable blockouts. Dimensions are clamped to a
  supported range; scene detail depends on the sketch and selected model.
- The Blender MCP add-on's own tools may have additional capabilities, but this
  agent only uses `execute_blender_code` to add validated primitives.
- Sketch images are sent to the selected AI provider. Review that provider's
  data and privacy terms before using sensitive images.

## License

No license has been added yet. Add one before accepting contributions or
granting reuse rights.
