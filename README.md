# Release Link Check

Catch broken GitHub Release download links in your README, documentation and install commands. Check which assets exist on the published release, or test where your `latest/download` links would land if you published a draft today.

[Download the Python script](https://github.com/Bratuha1/release-link-check/releases/latest/download/release_link_check.py)

## Try it

Run with Python 3.10 or newer. The script has no third-party dependencies.

```sh
python release_link_check.py README.md
```

For a real example, [Peripheral Battery Tray](https://github.com/Bratuha1/peripheral-battery-tray) links to its portable build:

```text
https://github.com/Bratuha1/peripheral-battery-tray/releases/download/v1.0.0/PeripheralBatteryTray-1.0.0-windows-x64-portable.zip
```

```text
README.md:5: OK 'PeripheralBatteryTray-1.0.0-windows-x64-portable.zip' in Bratuha1/peripheral-battery-tray 'v1.0.0'
1 link(s): 1 OK, 0 missing, 0 error(s)
```

Pass additional file paths to scan more documents. The checker reports the file and line number for each match. It reads release and asset metadata from the GitHub API, so it can check large installers without downloading them. A release is fetched once per repository and selector.

## Run in GitHub Actions

Add this after checkout in a pull-request workflow. The runner needs Python 3.10 or newer. On public repos the token is optional. It provides a higher API rate limit and can also read releases in the workflow's own private repo when granted `contents: read`.

```yaml
- uses: Bratuha1/release-link-check@v0.1.0
  with:
    files: |
      README.md
      docs/install.md
  env:
    GH_TOKEN: ${{ github.token }}
```

Omit `files` to check just `README.md`. Use one path per line.

## Check a draft before publishing

Get the draft's numeric **release ID** from the workflow that created it or list releases with `gh api repos/OWNER/REPO/releases --jq '.[] | [.id, .tag_name, .draft] | @tsv'`. Set `GH_TOKEN` or `GITHUB_TOKEN` to a GitHub token that can see that draft.

```sh
python release_link_check.py README.md --candidate OWNER/REPO=123456789
```

Only `latest/download` links for that repository are checked against the candidate. URLs pinned to a tag still check their exact tag. For example, if the README points to a stable `app-windows.zip` and the draft contains only `app-windows-v2.zip`, the check reports the stable name as missing.

## What it checks

- Absolute HTTPS download URLs on github.com, including literal URLs in code blocks and text files. Both tag-pinned downloads and `latest/download` are supported.
- Exact, case-sensitive asset names in the uploaded state. Use URL encoding for spaces and punctuation that Markdown treats as delimiters.
- Exit status `0` when all matched links pass, `1` when at least one asset is missing, and `2` for input or API errors. A file set with zero matching links exits `2`.

GitHub-generated source archives and release page links are outside the check. A passing result confirms uploaded asset metadata at check time. Browser/CDN availability and file contents require separate verification.

[Русская инструкция](docs/ru.md) · [MIT License](LICENSE)
