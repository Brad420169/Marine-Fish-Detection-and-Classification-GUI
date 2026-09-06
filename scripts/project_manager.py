"""
project_manager.py
------------------
Handles project creation, loading, and run tracking.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


PROJECTS_DIR = Path(__file__).parent.parent / "projects"



@dataclass
class RunRecord:
    run_number: int
    model_name: str
    timestamp: str
    output_dir: str


@dataclass
class Project:
    name: str
    created: str
    output_root: str = ""
    runs: list[RunRecord] = field(default_factory=list)

    # ── Paths ──────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return PROJECTS_DIR / self.name

    @property
    def meta_path(self) -> Path:
        return self.path / "project.json"

    # ── Persistence ────────────────────────────────────────

    def save(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        data = {
            "name":    self.name,
            "created": self.created,
            "output_root": self.output_root,
            "runs": [
                {
                    "run_number": r.run_number,
                    "model_name": r.model_name,
                    "timestamp":  r.timestamp,
                    "output_dir": r.output_dir,
                }
                for r in self.runs
            ],
        }
        self.meta_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, project_dir: Path) -> "Project":
        meta = project_dir / "project.json"
        data = json.loads(meta.read_text(encoding="utf-8"))
        runs = [
            RunRecord(
                run_number=r["run_number"],
                model_name=r["model_name"],
                timestamp=r["timestamp"],
                output_dir=r["output_dir"],
            )
            for r in data.get("runs", [])
        ]
        return cls(
            name=data["name"],
            created=data["created"],
            output_root=data.get("output_root", ""),
            runs=runs,
        )

    # ── Run tracking ───────────────────────────────────────

    def next_run_number(self, model_name: str) -> int:
        """Return the next run number for a given model."""
        existing = [
            r.run_number
            for r in self.runs
            if r.model_name == model_name
        ]
        return max(existing, default=0) + 1

    def resolve_output_dir(self, base_output: Path, model_name: str) -> Path:
        """
        Build:  base_output / project_name / model_name / run_X
        """
        run_number = self.next_run_number(model_name)
        run_dir = (
            base_output
            / self.name
            / model_name
            / f"run_{run_number}"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def record_run(self, model_name: str, output_dir: Path) -> RunRecord:
        """Add a completed run and save."""
        record = RunRecord(
            run_number=self.next_run_number(model_name),
            model_name=model_name,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            output_dir=str(output_dir),
        )
        self.runs.append(record)
        self.save()
        return record


# ── Helpers ────────────────────────────────────────────────

def list_projects() -> list[Project]:
    """Return all valid projects found in the projects folder."""
    if not PROJECTS_DIR.exists():
        return []
    projects = []
    for d in sorted(PROJECTS_DIR.iterdir()):
        if d.is_dir() and (d / "project.json").exists():
            try:
                projects.append(Project.load(d))
            except Exception:
                pass
    return projects


def create_project(name: str, output_root: str) -> Project:
    name = name.strip()
    output_root = output_root.strip()
    if not name:
        raise ValueError("Project name cannot be empty.")
    if not output_root:
        raise ValueError("Output folder cannot be empty.")
    dest = PROJECTS_DIR / name
    if dest.exists():
        raise ValueError(f"A project named '{name}' already exists.")
    project = Project(
        name=name,
        created=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        output_root=output_root,
    )
    project.save()
    return project

def delete_project(project: Project) -> None:
    """Permanently delete project metadata without deleting detection outputs."""
    project_dir = PROJECTS_DIR / project.name

    if not project_dir.exists():
        raise FileNotFoundError(
            f"Project folder does not exist: {project_dir}"
        )

    shutil.rmtree(project_dir)

