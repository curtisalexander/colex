"""Register a generated plugin with machine-specific paths; preserve other settings."""

import json
import os
import shutil
from pathlib import Path


def configure(uninstall: bool = False) -> None:
    root = Path(__file__).resolve().parents[2]
    home = Path(os.environ.get("SNOWFLAKE_HOME", Path.home() / ".snowflake"))
    config = home / "cortex"
    plugin = config / "colex-plugin"
    settings_path = config / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    plugins = settings.get("plugins", [])
    if not isinstance(plugins, list) or not all(isinstance(p, str) for p in plugins):
        raise ValueError("settings.json plugins must be an array of directory strings")
    plugins = [p for p in plugins if p != str(plugin)]
    if not uninstall:
        uv = shutil.which("uv")
        if not uv:
            raise ValueError("Install uv and put it on PATH first")
        if not (root / "uv.lock").is_file():
            raise ValueError("Install from a Colex checkout containing uv.lock")
        plugin.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root / ".cortex-plugin", plugin / ".cortex-plugin", dirs_exist_ok=True)
        shutil.copytree(root / "skills", plugin / "skills", dirs_exist_ok=True)
        (plugin / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "colex": {
                            "type": "stdio",
                            "command": uv,
                            "args": [
                                "run",
                                "--locked",
                                "--no-dev",
                                "--directory",
                                str(root),
                                "colex",
                                "serve",
                            ],
                        }
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        plugins.append(str(plugin))
    settings["plugins"] = plugins
    config.mkdir(parents=True, exist_ok=True)
    if settings_path.exists():
        shutil.copy2(settings_path, settings_path.with_suffix(".json.bak"))
    temporary = settings_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    temporary.replace(settings_path)
    if uninstall:
        print("Unregistered Colex. Existing logs and generated plugin files were retained.")
    else:
        print(f"Registered Colex at {plugin}")
        print("Restart CoCo or run /plugin reload, then check /mcp and /skill list.")
