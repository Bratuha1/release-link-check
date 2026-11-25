# Release Link Check

Find broken **Download** links before users click them. Release Link Check reads the links in your README and install commands, checks the files uploaded to GitHub, and shows where to fix each broken link.

[Download the Python script](https://github.com/Bratuha1/release-link-check/releases/latest/download/release_link_check.py)

## Check your README

Download the script into the folder that contains your `README.md`. Run it there with Python 3.10 or newer. No packages to install.

```sh
python release_link_check.py
```

For a real example, [Peripheral Battery Tray](https://github.com/Bratuha1/peripheral-battery-tray) links to its portable build:

```text
https://github.com/Bratuha1/peripheral-battery-tray/releases/download/v1.0.0/PeripheralBatteryTray-1.0.0-windows-x64-portable.zip
```

```text
README.md:5: OK 'PeripheralBatteryTray-1.0.0-windows-x64-portable.zip' in Bratuha1/peripheral-battery-tray 'v1.0.0'
1 link(s): 1 OK, 0 missing, 0 error(s)
```

If a file was renamed, the checker suggests similarly named files that are actually uploaded. Here it found `app-windows-v2.zip` on the release, but your link still asks for `app-windows-v1.zip`:

```text
README.md:8: MISSING 'app-windows-v1.zip' in owner/app 'v2.0.0'. Similar uploaded names: 'app-windows-v2.zip'
```

Pass file paths to check more documents with `python release_link_check.py README.md docs/install.md`. The checker uses GitHub release metadata and leaves the files in your repository untouched.

## Run in GitHub Actions

Save this as `.github/workflows/release-links.yml`. Broken links fail the job and appear as annotations next to the affected lines. The runner needs Python 3.10 or newer.

```yaml
name: Release links
on: [pull_request, workflow_dispatch]
permissions:
  contents: read
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: '3.13'
      - uses: Bratuha1/release-link-check@v0.2.0
```

The default is `README.md`. To check other files, pass one path per line:

```yaml
- uses: Bratuha1/release-link-check@v0.2.0
  with:
    files: |
      README.md
      docs/install.md
```

The action uses the workflow token by default. Set `GH_TOKEN` on the step when it needs a different token, for example to see a draft that the workflow token cannot access.

## Check a draft before publishing

For a planned release called `v2.0.0`, upload the files to a draft and run this with `GH_TOKEN` or `GITHUB_TOKEN` set to a token that can see the draft.

```sh
python release_link_check.py --candidate-tag v2.0.0
```

This works when all `latest/download` links point to the same repository. When they point to several repos, use `--candidate-tag OWNER/REPO=v2.0.0`. The GitHub Action accepts the same value as its `candidate-tag` input. Links pinned to a specific version still check that version.

Use `--candidate OWNER/REPO=123456789` to select a draft by its numeric release ID.

## What it checks

- Absolute HTTPS download URLs on github.com, including literal URLs in code blocks and text files. Both tag-pinned downloads and `latest/download` are supported. URLs assembled with shell variables such as `$VERSION` or `${ARCH}` are skipped.
- Exact, case-sensitive asset names in the uploaded state. Use URL encoding for spaces and punctuation that Markdown treats as delimiters.
- Exit status `0` when all matched links pass, `1` when at least one asset is missing, and `2` for input or API errors. A file set with zero matching links exits `2`.

GitHub-generated source archives and release page links are outside the check. A passing result confirms uploaded asset metadata at check time. Browser/CDN availability and file contents require separate verification.

[Русская инструкция](docs/ru.md) · [MIT License](LICENSE)
