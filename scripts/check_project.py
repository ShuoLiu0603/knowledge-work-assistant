from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the project quality gate.")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip the frontend TypeScript/Vite build.")
    parser.add_argument("--skip-compose", action="store_true", help="Skip Docker Compose config validation.")
    parser.add_argument("--with-smoke", action="store_true", help="Run the end-to-end smoke demo against a running stack.")
    args = parser.parse_args()

    steps = [
        (
            "backend tests",
            [sys.executable, "-m", "unittest", "discover", "-s", "apps/backend/tests"],
            {"PYTHONPATH": str(ROOT / "apps" / "backend")},
        ),
        (
            "python compile",
            [sys.executable, "-m", "compileall", "-q", "apps/backend/app", "apps/backend/tests", "scripts"],
            {"PYTHONPATH": str(ROOT / "apps" / "backend")},
        ),
        (
            "migration verification",
            [sys.executable, "scripts/verify_migrations.py"],
            {"PYTHONPATH": str(ROOT / "apps" / "backend")},
        ),
    ]

    if not args.skip_frontend:
        steps.append(("frontend build", ["npm", "run", "build"], None, ROOT / "apps" / "frontend"))

    if not args.skip_compose:
        steps.extend(
            [
                (
                    "docker compose config",
                    ["docker", "compose", "-f", "infra/docker-compose.yml", "--env-file", ".env.example", "config", "--quiet"],
                    {"APP_ENV_FILE": "../.env.example"},
                ),
                (
                    "production docker compose config",
                    [
                        "docker",
                        "compose",
                        "-f",
                        "infra/docker-compose.prod.yml",
                        "--env-file",
                        ".env.production.example",
                        "config",
                        "--quiet",
                    ],
                    {"APP_ENV_FILE": "../.env.production.example"},
                ),
            ]
        )

    if args.with_smoke:
        steps.append(("smoke demo", [sys.executable, "scripts/smoke_demo.py"], None))

    for step in steps:
        name, command = step[0], step[1]
        extra_env = step[2] if len(step) > 2 else None
        cwd = step[3] if len(step) > 3 else ROOT
        print(f"\n==> {name}", flush=True)
        result = run(command, cwd=cwd, extra_env=extra_env)
        if result != 0:
            return result

    print("\nQuality gate passed.")
    return 0


def run(command: list[str], cwd: Path, extra_env: dict[str, str] | None = None) -> int:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    executable = shutil.which(command[0])
    if executable is None:
        print(f"Command not found: {command[0]}", file=sys.stderr)
        return 127
    completed = subprocess.run([executable, *command[1:]], cwd=cwd, env=env, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
