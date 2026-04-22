# syk4y

`syk4y` is a shell CLI to automate Kaggle artifact workflows for a project:
- scaffold Kaggle dataset folders per artifact (`checkpoints`, `datasets`, `models`, `wheelhouse`)
- optionally generate `gen-full-repo.py`
- upload only changed artifacts to Kaggle
- build and reuse offline `wheelhouse.zip`

## Quick Start (5 minutes)

```bash
# 1) Install
sudo apt update
sudo apt install -y syk4y

# 2) Check environment
syk4y doctor

# 3) Initialize Kaggle artifact folders (first time per repo)
syk4y init checkpoints datasets models wheelhouse

# 4) Configure Kaggle credentials (interactive)
syk4y kaggle login

# 5) Upload changed artifacts
syk4y kaggle upload
```

## Install

### Install via apt (signed repo)

```bash
curl -fsSL https://taiduc1001.github.io/syk4y-apt/keys/syk4y-archive-keyring.gpg \
  | sudo tee /usr/share/keyrings/syk4y-archive-keyring.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/syk4y-archive-keyring.gpg] https://taiduc1001.github.io/syk4y-apt stable main" \
  | sudo tee /etc/apt/sources.list.d/syk4y.list

sudo apt update
sudo apt install -y syk4y
```

### Runtime notes

- `syk4y` needs a usable Python interpreter at runtime.
- Python selection order is user-friendly:
  1. `PYTHON_BIN` (if set)
  2. local venv in current repo (`.venv`, `venv`, `.env`, `env`, and similar names)
  3. `uv python find`
  4. system `python3` / `python`
- For best results, keep a project-local venv.

Example local setup:

```bash
uv venv .venv
uv pip install kaggle
```

## Get Kaggle Username and API Key

You need Kaggle credentials for `syk4y kaggle upload`.

1. Log in to `https://www.kaggle.com`.
2. Open `Account` settings.
3. In `API`, click `Create New Token`.
4. Kaggle downloads `kaggle.json` containing:
   - `username`
   - `key`

Ways to use credentials:

- Interactive:

```bash
syk4y kaggle login
```

- Explicit flags:

```bash
syk4y kaggle login --username <kaggle_username> --key <kaggle_api_key>
```

- Environment variables:

```bash
export KAGGLE_USERNAME=<kaggle_username>
export KAGGLE_KEY=<kaggle_api_key>
syk4y kaggle upload
```

Tip: your Kaggle username is also in your profile URL: `https://www.kaggle.com/<username>`.

If you already have a downloaded `kaggle.json`, you can inspect it:

```bash
cat /path/to/kaggle.json
```

Typical content:

```json
{"username":"your_username","key":"your_api_key"}
```

## Common Workflows

### 1) First-time setup for a repo

```bash
cd /path/to/your/repo
syk4y init checkpoints datasets models wheelhouse
```

What this does:
- creates `kaggle_upload/` layout
- creates per-artifact dataset metadata
- builds or updates `wheelhouse.zip` when `wheelhouse` is included
- does not require Kaggle login for initialization

### 2) Upload artifacts to Kaggle

Run `syk4y init ...` (or `syk4y gen`) first so dataset metadata exists.

```bash
syk4y kaggle upload
```

Useful flags:

```bash
syk4y kaggle upload --force
syk4y kaggle upload --message "weekly refresh"
syk4y kaggle upload --dir-mode zip
```

### 3) Build wheelhouse only (no Kaggle auth required)

```bash
syk4y kaggle upload --build-wheel-only
```

### 4) Install dependencies from wheelhouse offline

After you have `wheelhouse.zip`, extract it first:

```bash
unzip wheelhouse.zip -d wheelhouse
```

Install with uv (recommended, usually faster):

```bash
uv pip install --offline --no-index --find-links /path/to/wheelhouse <package-or-requirements>
```

Install with pip:

```bash
pip install --no-index --find-links /path/to/wheelhouse <package-or-requirements>
```

### 5) Generate full repo snapshot script + scaffold upload folders

```bash
syk4y gen
```

`gen` requires a git work tree unless you pass `--skip-gen`.

## Command Reference

### Top-level

```bash
syk4y [--repo-root DIR] <command>
```

Commands:
- `init`   setup Kaggle artifact folders and metadata (no git required)
- `gen`    generate `gen-full-repo.py` and setup folders (git required unless `--skip-gen`)
- `kaggle` Kaggle subcommands (`login`, `upload`)
- `doctor` environment and readiness checks

### `syk4y init`

```bash
syk4y init [--repo-root DIR] [options] <artifact...>
```

Options:
- `-u, --upload-dir DIR`
- `-w, --wheelhouse FILE`

Artifacts:
- `checkpoints`
- `datasets`
- `models`
- `wheelhouse`

### `syk4y gen`

```bash
syk4y gen [options]
```

Key options:
- `--repo-root DIR`
- `--skip-gen`
- `-a, --artifact NAME` (repeatable)
- `-o, --out FILE`
- `-w, --wheelhouse FILE`
- `-u, --upload-dir DIR`

### `syk4y kaggle login`

```bash
syk4y kaggle login [--username USERNAME] [--key KEY] [--force] [--non-interactive]
```

### `syk4y kaggle upload`

```bash
syk4y kaggle upload [--repo-root DIR] [options]
```

Key options:
- `-u, --upload-dir DIR`
- `--force`
- `-m, --message TEXT`
- `--dir-mode MODE` (`skip|zip|tar`)
- `--build-wheel-only`

### `syk4y doctor`

```bash
syk4y doctor [--repo-root DIR] [--json]
```

Use this first when troubleshooting:

```bash
syk4y doctor
syk4y doctor --json
```

## Practical Examples

### Initialize only `datasets` + `models`

```bash
syk4y init datasets models
```

### Run from outside repo

```bash
syk4y --repo-root /path/to/repo init checkpoints wheelhouse
syk4y --repo-root /path/to/repo kaggle upload
```

### Setup-only mode (no snapshot generation)

```bash
syk4y gen --skip-gen -a checkpoints -a wheelhouse
```

### Non-interactive login (CI style)

```bash
syk4y kaggle login --username "$KAGGLE_USERNAME" --key "$KAGGLE_KEY" --non-interactive
```

## Troubleshooting

### "Kaggle credentials are not configured"

```bash
syk4y kaggle login
```

or set env vars:

```bash
export KAGGLE_USERNAME=...
export KAGGLE_KEY=...
```

### Python not found / pip unavailable

Recommended fix:

```bash
uv venv .venv
uv pip install kaggle
```

Then retry command in the same repo.

### Upload skips because nothing changed

This is expected change-detection behavior.

To force upload:

```bash
syk4y kaggle upload --force
```

## Security Notes

- Never commit `~/.kaggle/kaggle.json`.
- Prefer environment variables in CI.
- Rotate Kaggle API key if exposed.

## Maintainer Notes (Packaging)

For package/repo maintainers only:

```bash
scripts/build-deb.sh --version 0.1.0
scripts/build-apt-repo.sh --output-dir site --deb dist/deb/syk4y_0.1.0_all.deb
scripts/sign-apt-release.sh --repo-dir site --suite stable --key-id <KEY_ID>
```
