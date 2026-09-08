#!/usr/bin/env python3
"""Run after deployment. This checks the real server, not a simulated Blender."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def run(arguments, timeout=60):
    result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed: {arguments[0:4]}\n{result.stderr[-2000:]}")
    return result.stdout.strip()


if __name__ == "__main__":
    if not shutil.which("docker"):
        sys.exit("Docker is unavailable on this machine. No Blender verification was run.")
    try:
        run(["docker", "compose", "config", "--quiet"])
        result = run(["docker", "compose", "exec", "-T", "--user", "1000:1000", "blender",
                      "blender", "--background", "--factory-startup", "--threads", "2",
                      "--python-exit-code", "9", "--python", "/opt/workstation-checks/smoke_blender.py"],
                     timeout=300)
        marker = "BLENDER_WORKSTATION_CHECK="
        lines = [line[len(marker):] for line in result.splitlines() if line.startswith(marker)]
        if not lines:
            raise RuntimeError("Blender did not report a successful check.")
        report = json.loads(lines[-1])
        report["verified_at"] = datetime.now(timezone.utc).isoformat()
        ids = run(["docker", "compose", "images", "-q"]).splitlines()
        report["images"] = json.loads(run(["docker", "image", "inspect", *dict.fromkeys(ids)]))
        # Retain only image provenance, never container configuration or environment secrets.
        report["images"] = [{"id": x["Id"], "digests": x.get("RepoDigests", [])} for x in report["images"]]
        (ROOT / "deploy-lock.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        print("Next: test opening, editing, saving and reopening through the target browser.")
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
        sys.exit(str(error))
