"""
Z-Image-Turbo MLX runner — called as a subprocess by map_generator.py.

Usage:
    python zimage_runner.py <prompt> <output_path> [width] [height] [num_steps] [seed]

Prints a JSON result dict to stdout.
"""

import json
import sys
from pathlib import Path

MODEL_PATH = Path("/Users/ckennedy/.omlx/models/illusion615/Z-Image-Turbo-MLX")

if not MODEL_PATH.exists():
    print(json.dumps({"status": "error", "error": f"Model path not found: {MODEL_PATH}"}))
    sys.exit(1)

# Add model dir to path so relative imports in pipeline.py work
parent = str(MODEL_PATH.parent)
if parent not in sys.path:
    sys.path.insert(0, parent)

model_name = MODEL_PATH.name

try:
    import importlib
    mod = importlib.import_module(f"{model_name}.pipeline")
    ZImageMLXPipeline = mod.ZImageMLXPipeline
except Exception as e:
    print(json.dumps({"status": "error", "error": f"Import failed: {e}"}))
    sys.exit(1)


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"status": "error", "error": "Usage: zimage_runner.py <prompt> <output_path> [width] [height] [steps] [seed]"}))
        sys.exit(1)

    prompt = sys.argv[1]
    output_path = sys.argv[2]
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 768
    height = int(sys.argv[4]) if len(sys.argv) > 4 else 768
    num_steps = int(sys.argv[5]) if len(sys.argv) > 5 else 8
    seed = int(sys.argv[6]) if len(sys.argv) > 6 else None

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    pipeline = ZImageMLXPipeline()
    pipeline.load(MODEL_PATH)
    result = pipeline.generate_and_save(
        prompt=prompt,
        output_path=output_path,
        width=width,
        height=height,
        num_steps=num_steps,
        seed=seed,
    )
    result["status"] = "success"
    print(json.dumps(result))


if __name__ == "__main__":
    main()
