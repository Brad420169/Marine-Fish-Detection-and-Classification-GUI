"""
project_manager.py
------------------
Handles project creation, loading, and run tracking.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
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

    items: list[dict] = field(default_factory=list)

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
        import os
        import tempfile
        fd, temporary = tempfile.mkstemp(dir=self.path, suffix='.json.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(asdict(self), stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.meta_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

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
            items=data.get("items", []),
        )

    def add_item(self, kind, name, path, **details):
        path = str(Path(path).resolve())
        previous = list(self.items)
        item = dict(kind=kind, name=name, path=path, **details)
        self.items = [entry for entry in self.items
                      if not (entry['kind'] == kind and entry['path'] == path)] + [item]
        try:
            self.save()
        except Exception:
            self.items = previous
            raise
        return item

    def remove_work(self, item):
        """Forget one project entry; source data and outputs are never deleted."""
        old_runs, old_items = self.runs, self.items
        if item['kind'] == 'run':
            self.runs = [run for run in self.runs if run != item['record']]
        else:
            self.items = [entry for entry in self.items
                          if (entry['kind'], entry['path']) != (item['kind'], item['path'])]
        try:
            self.save()
        except Exception:
            self.runs, self.items = old_runs, old_items
            raise

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
        parent = base_output / self.name / model_name
        parent.mkdir(parents=True, exist_ok=True)
        while True:
            run_dir = parent / f'run_{run_number}'
            try:
                run_dir.mkdir()
                return run_dir
            except FileExistsError:
                run_number += 1

    def record_run(self, model_name: str, output_dir: Path) -> RunRecord:
        """Add a completed run and save."""
        record = RunRecord(
            run_number=(int(output_dir.name[4:]) if output_dir.name.startswith('run_')
                        and output_dir.name[4:].isdigit() else self.next_run_number(model_name)),
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
    """Move project-owned folders to the native trash; never permanently delete."""
    from PyQt6.QtCore import QFile
    if not project.name or Path(project.name).name != project.name or project.name in ('.', '..'):
        raise ValueError('Invalid project folder name.')
    project_dir = PROJECTS_DIR / project.name
    if not project_dir.is_dir():
        raise FileNotFoundError(f'Project folder does not exist: {project_dir}')
    roots = []
    if project.output_root:
        roots.append(Path(project.output_root) / project.name)
    # Retain ownership of runs even if the output location changed later.
    for run in project.runs:
        path = Path(run.output_dir)
        if path.parent.parent.name == project.name and path.name.startswith('run_'):
            roots.append(path.parent.parent)
    for item in project.items:
        path = Path(item['path'])
        if item['kind'] == 'annotation' and path.parent.name == 'annotations' and path.parent.parent.name == project.name:
            roots.append(path.parent.parent)
    candidates = []
    for path in [*roots, project_dir]:
        if not path.exists(): continue
        if path.is_symlink():
            raise ValueError(f'Cannot trash a linked project folder: {path}')
        resolved = path.resolve()
        if (resolved.name != project.name or resolved == Path.home().resolve()
                or resolved == PROJECTS_DIR.resolve() or resolved in PROJECTS_DIR.resolve().parents):
            raise ValueError(f'Unsafe project folder: {path}')
        if resolved not in candidates: candidates.append(resolved)
    candidates = [path for path in candidates if not any(parent in path.parents for parent in candidates)]
    # Outputs first, metadata last: failed trash operations leave the project
    # discoverable for retry. Qt uses Recycle Bin, Trash or freedesktop trash.
    for path in candidates:
        success, _ = QFile.moveToTrash(str(path))
        if not success:
            raise OSError(f'Could not move {path} to the system trash. Nothing was permanently deleted. '
                          'Any folders already moved remain recoverable in the trash; retry after resolving the problem.')
