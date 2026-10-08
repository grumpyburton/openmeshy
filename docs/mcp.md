# Let Claude drive it (MCP)

`openmeshy_mcp/` is an [MCP](https://modelcontextprotocol.io) server. With it, Claude Code
(or any MCP client) can run the whole pipeline: "turn `hero.png` into a rigged Unity
character and put it in `~/MyGame`", or start from words alone: "make me a goblin for
Unity".

## Set up

```bash
.venv/bin/python -m pip install -r requirements-mcp.txt   # once
```

This repo ships `.mcp.json`, so Claude Code started in this folder offers the
`openmeshy` server; approve it when asked. To use it in every project (a game's Unity
project, say), add it once at user level:

```bash
claude mcp add --scope user -e PYTHONPATH=/path/to/repo -- \
    openmeshy /path/to/repo/.venv/bin/python -m openmeshy_mcp.server
```

In another project, relative paths mean that project's files. Pictures and models from
outside the repo are copied into `output/mcp/inputs/` for the lab, results land in the
repo's `output/`, and `install_to_unity(folder)` drops a result into the current Unity
project (`Assets/OpenMeshy/<name>/`, importer included).

The server starts the lab (`viewer/serve.py`) itself if it is not running.

## Tools

| Tool | What it does | Time |
|---|---|---|
| `generate_image` | text → PNG (Qwen-Image, non-commercial licence) | seconds on NVIDIA/AMD, 4–12 min on a Mac |
| `image_to_unity` | image → rigged, textured Unity folder + zip | 13–16 min |
| `generate_3d` | image → textured GLB; Pixal3D by default, or any other backend installed here (`backend=`) | 1–15 min |
| `finish_model` | retopology to a face budget, detail bake, Pixel Match | ~30 s |
| `autorig` | skeleton + skin weights (SkinTokens, else template) | ~1 min |
| `rebind_rig` | apply joint corrections from Rig Review (model + .blend + .rig.json) and re-skin | ~1 min |
| `export_unity` | GLB (+ LODs) → FBX, URP textures, manifest; optional copy into a project | seconds |
| `split_props` | prop-sheet GLB → separate named props with LODs | minutes |
| `export_props_unity` | every finished prop → one FBX each, LODGroup-ready | ~1 min |
| `install_to_unity` | copy a finished Unity folder (+ importer) into a Unity project | instant |
| `job_status` / `cancel_job` | follow or stop a job; `wait_seconds` blocks up to 15 min | |
| `render_preview` | front + side picture of a GLB, returned as an image | ~3 s |
| `list_outputs`, `lab_status` | recent results; what is installed | |

Long jobs return a `job_id` at once. Claude then calls `job_status(kind, job_id,
wait_seconds=600)`.

## How it fits together

The server is a thin client of the lab's HTTP API. Jobs an agent starts sit in the same
one-at-a-time queue as the browser's and show in its tabs, so two never share the GPU. A
`409` answer means something else is running; wait and retry.

## Guardrails

- **No downloads.** Nothing here installs software or fetches weights. `lab_status` says
  what is missing; a person installs it from Setup & Status, after seeing size and licence.
- **Paths stay local.** Tool jobs only read files inside the repo or the data drive. A
  Unity project may live anywhere, but must have `Assets/` and `ProjectSettings/`.
- **Provenance is unchanged.** Every result still carries its licence record.
