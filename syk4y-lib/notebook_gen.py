#!/usr/bin/env python3
import copy
import json
import re
import sys
from pathlib import Path
from typing import Optional

_lib = Path(__file__).parent
sys.path.insert(0, str(_lib))

from jobs import Job


def _slugify(text: str, max_len: int = 40) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:max_len].rstrip("-")


def _find_script_lines(source: str) -> list[str]:
    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if re.match(r"^!bash\s+\S+", stripped):
            lines.append(stripped)
        elif re.match(r"^#\s*!bash\s+\S+", stripped):
            uncommented = re.sub(r"^#\s*", "", stripped)
            lines.append(uncommented)
    return lines


def parse_notebook_jobs(notebook_path: Path) -> list[Job]:
    raw = json.loads(notebook_path.read_text(encoding="utf-8"))
    cells = raw.get("cells", [])
    jobs = []
    seen = set()
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source", []))
        for script_line in _find_script_lines(source):
            if script_line in seen:
                continue
            seen.add(script_line)
            path_parts = script_line.split("/")
            last_two = "/".join(path_parts[-2:]) if len(path_parts) >= 2 else path_parts[-1]
            job_id = _slugify(last_two.replace(".sh", ""))
            jobs.append(Job(job_id=job_id, script_line=script_line))
    return jobs


def _toggle_source(source: str, active_script_line: str) -> str:
    lines = source.splitlines(keepends=True)
    result = []
    for line in lines:
        stripped = line.strip()
        is_bash_line = bool(re.match(r"^!bash\s+\S+", stripped))
        is_commented_bash = bool(re.match(r"^#\s*!bash\s+\S+", stripped))

        if is_bash_line:
            raw_line = stripped
            if raw_line == active_script_line:
                result.append(line)
            else:
                indent = len(line) - len(line.lstrip())
                result.append(" " * indent + "# " + stripped + ("\n" if line.endswith("\n") else ""))
        elif is_commented_bash:
            uncommented = re.sub(r"^#\s*", "", stripped)
            if uncommented == active_script_line:
                indent = len(line) - len(line.lstrip())
                result.append(" " * indent + uncommented + ("\n" if line.endswith("\n") else ""))
            else:
                result.append(line)
        else:
            result.append(line)
    return "".join(result)


def generate_kernel_dir(
    job: Job,
    account: dict,
    run_id: str,
    template_notebook: dict,
    dataset_sources: list[str],
    kernel_dir_path: Path,
) -> Path:
    kernel_dir_path.mkdir(parents=True, exist_ok=True)

    nb = copy.deepcopy(template_notebook)
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            source = "".join(cell.get("source", []))
            new_source = _toggle_source(source, job.script_line)
            if isinstance(cell["source"], list):
                cell["source"] = [new_source]
            else:
                cell["source"] = new_source

    nb_path = kernel_dir_path / "notebook.ipynb"
    nb_path.write_text(json.dumps(nb, indent=1) + "\n", encoding="utf-8")

    slug = _make_kernel_slug(run_id, job.job_id)
    metadata = {
        "id": f"{account['username']}/{slug}",
        "title": f"syk4y {run_id} {job.job_id}",
        "code_file": "notebook.ipynb",
        "language": "python",
        "kernel_type": "notebook",
        "is_private": True,
        "enable_gpu": True,
        "enable_internet": True,
        "dataset_sources": dataset_sources,
        "kernel_sources": [],
        "competition_sources": [],
    }
    meta_path = kernel_dir_path / "kernel-metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    return kernel_dir_path


def _make_kernel_slug(run_id: str, job_id: str) -> str:
    combined = f"{run_id}-{job_id}"
    return _slugify(combined, max_len=50)


def load_template_notebook(notebook_path: Path) -> dict:
    return json.loads(notebook_path.read_text(encoding="utf-8"))
