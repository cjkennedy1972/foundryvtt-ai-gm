#!/usr/bin/env python3
"""
ComfyUI Setup Verification Script

Verifies that ComfyUI is properly configured for campaign map generation.
Run this to diagnose any issues with your ComfyUI installation.

Usage:
    python verify_comfyui_setup.py
    python verify_comfyui_setup.py --verbose
    python verify_comfyui_setup.py --fix-config
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Dict, Any, Optional

try:
    import httpx
except ImportError:
    print("❌ httpx not installed. Install with: pip install httpx")
    sys.exit(1)


class ComfyUIVerifier:
    """Verify ComfyUI setup for map generation."""

    def __init__(self, base_url: str = "http://localhost:18188", verbose: bool = False):
        self.base_url = base_url.rstrip("/")
        self.verbose = verbose
        self.client = httpx.Client(timeout=10)
        self.results = {"passed": [], "failed": [], "warnings": []}

    def log(self, msg: str, level: str = "info"):
        """Log a message."""
        levels = {"info": "ℹ️", "ok": "✅", "error": "❌", "warning": "⚠️"}
        prefix = levels.get(level, "•")
        if level != "info" or self.verbose:
            print(f"{prefix} {msg}")

    def test_connection(self) -> bool:
        """Test if ComfyUI is running."""
        self.log("Testing ComfyUI connection...", "info")
        try:
            resp = self.client.get(f"{self.base_url}/system_stats", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                version = data.get("system", {}).get("comfyui_version", "unknown")
                self.log(f"✅ ComfyUI running (v{version})", "ok")
                self.results["passed"].append("ComfyUI connection")
                return True
            else:
                self.log(f"❌ ComfyUI returned {resp.status_code}", "error")
                self.results["failed"].append(f"ComfyUI HTTP {resp.status_code}")
                return False
        except Exception as e:
            self.log(f"❌ Cannot connect to {self.base_url}: {e}", "error")
            self.results["failed"].append(f"ComfyUI connection: {e}")
            return False

    def check_checkpoint(self) -> bool:
        """Check if required checkpoint is available."""
        self.log("Checking for dDBattlemapsSDXL10_upscaleV10.safetensors...", "info")
        try:
            resp = self.client.get(f"{self.base_url}/object_info/CheckpointLoaderSimple")
            if resp.status_code != 200:
                self.log("❌ Cannot query checkpoints", "error")
                self.results["failed"].append("Checkpoint query failed")
                return False

            data = resp.json()
            checkpoints = data.get("CheckpointLoaderSimple", {}).get("input", {}).get("required", {}).get("ckpt_name", [[]])[0]
            required = "dDBattlemapsSDXL10_upscaleV10.safetensors"

            if required in checkpoints:
                self.log(f"✅ Found {required}", "ok")
                self.results["passed"].append("Required checkpoint installed")
                return True
            else:
                self.log(f"❌ {required} NOT FOUND", "error")
                if self.verbose:
                    self.log(f"   Available checkpoints: {', '.join(checkpoints)}", "info")
                self.results["failed"].append("Required checkpoint missing")
                return False
        except Exception as e:
            self.log(f"❌ Error checking checkpoint: {e}", "error")
            self.results["failed"].append(f"Checkpoint check: {e}")
            return False

    def check_samplers(self) -> bool:
        """Check if optimal samplers are available."""
        self.log("Checking for optimal samplers...", "info")
        required_samplers = ["dpmpp_3m_sde", "dpmpp_2m_sde", "karras"]
        try:
            resp = self.client.get(f"{self.base_url}/object_info/KSampler")
            if resp.status_code != 200:
                self.log("❌ Cannot query samplers", "error")
                self.results["failed"].append("Sampler query failed")
                return False

            data = resp.json()
            sampler_list = data.get("KSampler", {}).get("input", {}).get("required", {}).get("sampler_name", [[]])[0]
            scheduler_list = data.get("KSampler", {}).get("input", {}).get("required", {}).get("scheduler", [[]])[0]

            missing = []
            for sampler in ["dpmpp_3m_sde", "dpmpp_2m_sde"]:
                if sampler not in sampler_list:
                    missing.append(f"sampler: {sampler}")

            if "karras" not in scheduler_list:
                missing.append("scheduler: karras")

            if missing:
                self.log(f"❌ Missing optimal components: {', '.join(missing)}", "error")
                self.results["failed"].append(f"Missing: {', '.join(missing)}")
                return False
            else:
                self.log("✅ All optimal samplers and schedulers available", "ok")
                self.results["passed"].append("Optimal samplers/schedulers")
                return True
        except Exception as e:
            self.log(f"❌ Error checking samplers: {e}", "error")
            self.results["failed"].append(f"Sampler check: {e}")
            return False

    def check_required_nodes(self) -> bool:
        """Check if all required nodes are available."""
        self.log("Checking required nodes...", "info")
        required = [
            "CheckpointLoaderSimple",
            "CLIPTextEncode",
            "KSampler",
            "EmptyLatentImage",
            "VAEDecode",
            "SaveImage"
        ]
        try:
            resp = self.client.get(f"{self.base_url}/object_info")
            if resp.status_code != 200:
                self.log("❌ Cannot query nodes", "error")
                self.results["failed"].append("Node query failed")
                return False

            data = resp.json()
            available = set(data.keys())
            missing = [n for n in required if n not in available]

            if missing:
                self.log(f"❌ Missing nodes: {', '.join(missing)}", "error")
                self.results["failed"].append(f"Missing nodes: {', '.join(missing)}")
                return False
            else:
                self.log("✅ All required nodes available", "ok")
                self.results["passed"].append("Required nodes")
                return True
        except Exception as e:
            self.log(f"❌ Error checking nodes: {e}", "error")
            self.results["failed"].append(f"Node check: {e}")
            return False

    def check_workflow_config(self) -> bool:
        """Check if workflow config file exists."""
        self.log("Checking workflow configuration file...", "info")
        config_path = Path(__file__).parent / "sdxl_battlemap_workflow.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = json.load(f)
                self.log(f"✅ Workflow config found (v{config.get('metadata', {}).get('version', '?')})", "ok")
                self.results["passed"].append("Workflow config")
                return True
            except Exception as e:
                self.log(f"❌ Workflow config invalid: {e}", "error")
                self.results["failed"].append(f"Workflow config: {e}")
                return False
        else:
            self.log(f"❌ Workflow config not found at {config_path}", "error")
            self.results["failed"].append("Workflow config missing")
            return False

    def run_all_checks(self) -> bool:
        """Run all verification checks."""
        print("\n" + "=" * 60)
        print("ComfyUI Setup Verification")
        print("=" * 60 + "\n")

        if not self.test_connection():
            print("\n❌ ComfyUI is not running or not accessible.")
            print(f"   Start it with: cd ComfyUI && python main.py --port 18188")
            return False

        checks = [
            self.check_checkpoint,
            self.check_samplers,
            self.check_required_nodes,
            self.check_workflow_config,
        ]

        for check in checks:
            check()
            print()

        # Print summary
        print("=" * 60)
        print("Summary")
        print("=" * 60)
        print(f"✅ Passed: {len(self.results['passed'])}")
        print(f"❌ Failed: {len(self.results['failed'])}")
        print(f"⚠️  Warnings: {len(self.results['warnings'])}")
        print()

        if self.results["failed"]:
            print("Failed checks:")
            for failure in self.results["failed"]:
                print(f"  • {failure}")
            print()
            return False
        else:
            print("✅ All checks passed! ComfyUI is ready for map generation.")
            return True

    def close(self):
        """Close the HTTP client."""
        self.client.close()


def main():
    parser = argparse.ArgumentParser(
        description="Verify ComfyUI setup for campaign map generation"
    )
    parser.add_argument(
        "--url",
        default="http://localhost:18188",
        help="ComfyUI base URL (default: http://localhost:18188)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--fix-config",
        action="store_true",
        help="Attempt to fix configuration issues (future)"
    )

    args = parser.parse_args()

    verifier = ComfyUIVerifier(base_url=args.url, verbose=args.verbose)
    try:
        success = verifier.run_all_checks()
        sys.exit(0 if success else 1)
    finally:
        verifier.close()


if __name__ == "__main__":
    main()
