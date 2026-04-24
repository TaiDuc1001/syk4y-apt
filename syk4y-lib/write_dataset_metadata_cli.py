import json
import sys
from pathlib import Path
from string import Template


def main() -> int:
    if len(sys.argv) != 10:
        print(
            "Usage: write_dataset_metadata_cli.py <metadata_path> <repo_name> "
            "<kaggle_username> <dataset_slug> <artifact> <artifact_id> "
            "<artifact_source> <artifact_item_name> <template_dir>",
            file=sys.stderr,
        )
        return 2

    metadata_path = Path(sys.argv[1])
    repo_name = sys.argv[2]
    kaggle_username = sys.argv[3]
    dataset_slug = sys.argv[4]
    artifact = sys.argv[5]
    artifact_id = sys.argv[6]
    artifact_source = sys.argv[7]
    artifact_item_name = sys.argv[8]
    template_dir = Path(sys.argv[9]).resolve(strict=False)

    metadata_template_path = template_dir / "dataset-metadata.json.tmpl"
    if not metadata_template_path.exists():
        raise FileNotFoundError(f"Missing template: {metadata_template_path}")

    template = Template(metadata_template_path.read_text(encoding="utf-8"))
    metadata_json = template.substitute(
        title=json.dumps(f"{repo_name} {artifact}"),
        id=json.dumps(f"{kaggle_username}/{dataset_slug}"),
        description=json.dumps(
            f"Artifact dataset for '{repo_name}': {artifact}. Managed by syk4y init/upload."
        ),
        syk4y_artifact=json.dumps(artifact),
        syk4y_artifact_id=json.dumps(artifact_id),
        syk4y_source=json.dumps(artifact_source),
        syk4y_item_name=json.dumps(artifact_item_name),
    )
    metadata = json.loads(metadata_json)

    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
