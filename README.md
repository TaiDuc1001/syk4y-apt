# syk4y

`syk4y` is a CLI to make Kaggle artifact workflows simple and repeatable:
- initialize per-artifact Kaggle dataset folders
- upload only changed artifacts
- build and reuse offline `wheelhouse.zip`
- optionally generate `kaggle_upload/gen-full.py`

## Start Here (Fast Path)

```bash
# 1) Install
sudo apt update
sudo apt install -y syk4y

# 2) Go to your repo
cd /path/to/your/repo

# 3) Check environment
syk4y doctor

# 4) Initialize artifact datasets (first time per repo)
syk4y init checkpoints datasets models wheelhouse

# 5) Configure Kaggle credentials (interactive)
syk4y kaggle login

# 6) Upload changed artifacts
syk4y kaggle upload
```

If this is all you need, you can stop here.

## Install

### apt (signed repository)

```bash
curl -fsSL https://taiduc1001.github.io/syk4y-apt/keys/syk4y-archive-keyring.gpg \
  | sudo tee /usr/share/keyrings/syk4y-archive-keyring.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/syk4y-archive-keyring.gpg] https://taiduc1001.github.io/syk4y-apt stable main" \
  | sudo tee /etc/apt/sources.list.d/syk4y.list

sudo apt update
sudo apt install -y syk4y
```

## Before You Run

### Python runtime

`syk4y` needs a usable Python interpreter.
Selection order:
1. `PYTHON_BIN` (if set)
2. local venv in repo (`.venv`, `venv`, `.env`, `env`, similar names)
3. `uv python find`
4. system `python3` / `python`

Recommended local setup:

```bash
uv venv .venv
uv pip install kaggle
```

### Kaggle credentials

You need Kaggle credentials for `syk4y kaggle upload`.

Get token:
1. Log in to `https://www.kaggle.com`
2. Open Account settings
3. In API section, click `Create New Token`

Use credentials in one of these ways:

Interactive:

```bash
syk4y kaggle login
```

Explicit flags:

```bash
syk4y kaggle login --username <kaggle_username> --key <kaggle_api_key>
```

Environment variables:

```bash
export KAGGLE_USERNAME=<kaggle_username>
export KAGGLE_KEY=<kaggle_api_key>
syk4y kaggle upload
```

## Common Tasks

### 1) First-time setup for a repo

```bash
cd /path/to/your/repo
syk4y init checkpoints datasets models wheelhouse
```

This creates `kaggle_upload/` layout and metadata for each artifact dataset.
If `wheelhouse` is included, `wheelhouse.zip` is built/updated.

### 2) Upload only what changed

```bash
syk4y kaggle upload
```

Upload selected artifacts only:

```bash
syk4y kaggle upload datasets
syk4y kaggle upload wheelhouse
```

Useful options:

```bash
syk4y kaggle upload --force
syk4y kaggle upload --message "weekly refresh"
syk4y kaggle upload --dir-mode zip
```

### 3) Build wheelhouse only (no Kaggle auth required)

```bash
syk4y kaggle upload --build-wheel-only
```

### 4) Install dependencies offline from wheelhouse

```bash
unzip wheelhouse.zip -d wheelhouse

# uv (recommended)
uv pip install --offline --no-index --find-links /path/to/wheelhouse <package-or-requirements>

# pip
pip install --no-index --find-links /path/to/wheelhouse <package-or-requirements>
```

### 5) Generate full repo snapshot script

```bash
syk4y gen
```

Notes:
- `gen` requires a git work tree unless you pass `--skip-gen`
- default output is `kaggle_upload/gen-full.py`

To generate snapshot and initialize artifact folders together:

```bash
syk4y gen -a checkpoints -a datasets -a models -a wheelhouse
```

## Command Cheat Sheet

### Top-level

```bash
syk4y [--repo-root DIR] <command>
```

Commands:
- `init` setup Kaggle artifact folders and metadata
- `gen` generate snapshot script (`gen-full.py`)
- `kaggle` Kaggle helpers and multi-account tools
- `doctor` environment/readiness checks

### `syk4y init`

```bash
syk4y init [--repo-root DIR] [options] <artifact...>
```

Options:
- `-u, --upload-dir DIR`
- `-w, --wheelhouse FILE`

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

### `syk4y kaggle`

```bash
syk4y kaggle <subcommand> [options]
```

Subcommands:
- `login` configure `~/.kaggle/kaggle.json`
- `upload` upload changed artifacts to Kaggle
- `account` manage account pool (`add/list/remove/enable/disable/test/set`)
- `run` multi-account notebook runner (`start/status/list/pull/stop/resume`)

### `syk4y doctor`

```bash
syk4y doctor [--repo-root DIR] [--json]
```

## Troubleshooting

### 1) "Kaggle credentials are not configured"

```bash
syk4y kaggle login
```

Or set env vars:

```bash
export KAGGLE_USERNAME=...
export KAGGLE_KEY=...
```

### 2) Python not found / pip unavailable

```bash
uv venv .venv
uv pip install kaggle
```

Then rerun command in the same repo.

### 3) Upload says nothing changed

This is expected change detection behavior.

To force upload:

```bash
syk4y kaggle upload --force
```

### 4) General health check

```bash
syk4y doctor
syk4y doctor --json
```

## Security Notes

- Never commit `~/.kaggle/kaggle.json`
- Prefer environment variables in CI
- Rotate Kaggle API key if exposed

## Maintainer Notes (Packaging)

For package/repo maintainers:

```bash
scripts/build-deb.sh --version 0.1.0
scripts/build-apt-repo.sh --output-dir site --deb dist/deb/syk4y_0.1.0_all.deb
scripts/sign-apt-release.sh --repo-dir site --suite stable --key-id <KEY_ID>
```
