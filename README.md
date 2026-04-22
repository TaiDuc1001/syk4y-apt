# syk4y apt

`syk4y` is a shell-based CLI for Kaggle artifact automation.

## Install via apt (signed)

After a release is published, install with signed-by:

```bash
curl -fsSL https://taiduc1001.github.io/syk4y-apt/keys/syk4y-archive-keyring.gpg \
  | sudo tee /usr/share/keyrings/syk4y-archive-keyring.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/syk4y-archive-keyring.gpg] https://taiduc1001.github.io/syk4y-apt stable main" \
  | sudo tee /etc/apt/sources.list.d/syk4y.list

sudo apt update
sudo apt install syk4y
```

## Commands

```bash
syk4y init <artifact...>
syk4y gen
syk4y build-wheel
syk4y upload
syk4y doctor
```

## One-time signing setup

1. Generate an APT signing key locally.

```bash
gpg --batch --quick-generate-key "syk4y APT <taiduc1001@users.noreply.github.com>" rsa4096 sign 2y
```

2. Get key id.

```bash
gpg --list-secret-keys --keyid-format LONG
```

3. Export private key for GitHub Actions secret.

```bash
gpg --armor --export-secret-keys <KEY_ID> > /tmp/syk4y-private.asc
```

4. Add GitHub repository secrets:
- `APT_GPG_PRIVATE_KEY`: content of `/tmp/syk4y-private.asc`
- `APT_GPG_PASSPHRASE`: key passphrase (empty if no passphrase)

Public key is committed in this repo at:
- `keys/syk4y-archive-keyring.gpg`
- `keys/syk4y-archive-keyring.asc`

## Release flow

- Push commit to `main` (no manual tag required)
- GitHub Actions will:
  1. Build `syk4y_<auto-version>_all.deb`
  2. Build static APT repo metadata (`dists/`, `pool/`)
  3. Sign `Release` (`InRelease` + `Release.gpg`)
  4. Publish public key under `keys/`
  5. Deploy to GitHub Pages
  6. Attach `.deb` to an auto-created prerelease tagged by commit

## Local packaging

```bash
scripts/build-deb.sh --version 0.1.0
scripts/build-apt-repo.sh --output-dir site --deb dist/deb/syk4y_0.1.0_all.deb
scripts/sign-apt-release.sh --repo-dir site --suite stable --key-id <KEY_ID>
```
