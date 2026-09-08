"""Run INSIDE real Blender. Produces independent .blend, PNG and GLB check artifacts."""
import json
from pathlib import Path
import tempfile

import bpy

output = Path("/config/workstation-checks")
output.mkdir(parents=True, exist_ok=True)
directory = Path(tempfile.mkdtemp(prefix="check-", dir=output))
scene = bpy.context.scene
cube = bpy.data.objects["Cube"]
cube.name = "Workstation verification cube"
bevel = cube.modifiers.new("Verification bevel", "BEVEL")
bevel.width = 0.15
bevel.segments = 3
cube.location.x = 0
cube.keyframe_insert(data_path="location", frame=1)
cube.location.x = 2
cube.keyframe_insert(data_path="location", frame=24)
scene.frame_set(1)
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 4
scene.render.resolution_x = 96
scene.render.resolution_y = 96
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = str(directory / "render.png")
bpy.ops.render.render(write_still=True)
bpy.ops.export_scene.gltf(filepath=str(directory / "scene.glb"), export_format="GLB")
bpy.ops.wm.save_as_mainfile(filepath=str(directory / "scene.blend"))
bpy.ops.wm.open_mainfile(filepath=str(directory / "scene.blend"))
assert "Workstation verification cube" in bpy.data.objects
bpy.context.scene.frame_set(24)
assert abs(bpy.data.objects["Workstation verification cube"].location.x - 2) < 0.001
assert (directory / "render.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
assert (directory / "scene.glb").read_bytes().startswith(b"glTF")
result = {
    "status": "passed", "blender_version": bpy.app.version_string,
    "checks": ["mesh modifier", "animation keyframes", "Cycles CPU render",
               "GLB export", "blend save and reopen"],
    "output_directory": str(directory),
    "browser_control_tested": False,
}
(directory / "result.json").write_text(json.dumps(result, indent=2))
print("BLENDER_WORKSTATION_CHECK=" + json.dumps(result))
