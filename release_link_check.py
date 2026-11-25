#!/usr/bin/env python3
"""Check GitHub release download URLs found in local Markdown and text files."""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


VERSION = "0.1.0"
API_HOST = "api.github.com"
REPO_PATTERN = r"[A-Za-z0-9-]+/(?!\.{1,2}(?:/|$))[A-Za-z0-9_.-]+"
DOWNLOAD_URL = re.compile(
    rf"""https://github\.com/(?P<repo>{REPO_PATTERN})/releases/"""
    r"""(?P<route>latest/download|download)/(?P<path>[^\s\x00-\x1f<>()\[\]{}"'\x60?#]+)"""
)


@dataclass(frozen=True)
class Link:
    file: Path
    line: int
    repo: str
    tag: str | None
    filename: str


@dataclass(frozen=True)
class Release:
    tag: str
    assets: frozenset[str]


def find_links(file, text):
    for number, line in enumerate(text.splitlines(), 1):
        for match in DOWNLOAD_URL.finditer(line):
            path = match["path"].rstrip(".,;:!")
            parent, slash, asset = path.rpartition("/")
            if match["route"] == "latest/download":
                if slash:
                    continue
                tag = None
            else:
                if not parent:
                    continue
                tag = unquote(parent)
                if tag in {".", ".."}:
                    continue
            if asset:
                yield Link(file, number, match["repo"], tag, unquote(asset))


class CheckError(Exception):
    """The release or its assets could not be inspected."""


class GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        destination = urlsplit(newurl)
        if destination.scheme != "https" or destination.netloc != API_HOST:
            raise CheckError("GitHub API returned a redirect outside api.github.com")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHub:
    def __init__(self, token=None):
        self.opener = build_opener(GitHubRedirect())
        self.headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"release-link-check/{VERSION}",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token.strip()}"
        self.cache = {}

    def get(self, path):
        request = Request(f"https://{API_HOST}{path}", headers=self.headers)
        try:
            with self.opener.open(request, timeout=15) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code == 404:
                raise CheckError("release not found or not accessible (HTTP 404)") from None
            if error.code == 429 or (error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0"):
                raise CheckError("GitHub API rate limit reached") from None
            raise CheckError(f"GitHub API returned HTTP {error.code}") from None
        except (OSError, URLError, ValueError) as error:
            raise CheckError(f"could not read the GitHub API response: {error}") from None

    def release(self, repo, selector):
        name = repo.lower()
        key = name, selector
        if key not in self.cache:
            try:
                self.cache[key] = self.fetch_release(name, selector)
            except CheckError as error:
                self.cache[key] = error
        result = self.cache[key]
        if isinstance(result, CheckError):
            raise result
        return result

    def fetch_release(self, repo, selector):
        base = f"/repos/{repo}/releases"
        data = self.get(f"{base}/{selector}")
        if not isinstance(data, dict) or type(data.get("id")) is not int or not isinstance(data.get("tag_name"), str):
            raise CheckError("GitHub API returned invalid release metadata")
        page = 1
        assets = set()
        while True:
            batch = self.get(f"{base}/{data['id']}/assets?per_page=100&page={page}")
            if not isinstance(batch, list) or any(
                not isinstance(asset, dict) or not isinstance(asset.get("name"), str)
                for asset in batch
            ):
                raise CheckError("GitHub API returned invalid asset metadata")
            assets.update(asset["name"] for asset in batch if asset.get("state") == "uploaded")
            if len(batch) < 100:
                return Release(data["tag_name"], frozenset(assets))
            page += 1


def parse_candidate(value):
    repo, separator, release_id = value.partition("=")
    if not separator or not re.fullmatch(REPO_PATTERN, repo) or not re.fullmatch(r"[1-9][0-9]*", release_id):
        raise argparse.ArgumentTypeError("expected OWNER/REPO=RELEASE_ID")
    return repo.lower(), release_id


def validate(link, github, candidates):
    target = f"tags/{quote(link.tag, safe='')}" if link.tag is not None else "latest"
    candidate = candidates.get(link.repo.lower()) if link.tag is None else None
    selector = candidate or target
    context = f"candidate release #{candidate}" if candidate else (
        ascii(link.tag) if link.tag is not None else "latest release"
    )
    try:
        release = github.release(link.repo, selector)
    except CheckError as error:
        return "ERROR", f"{link.repo} {context}: {error}"
    details = f"{ascii(link.filename)} in {link.repo} {ascii(release.tag)}"
    if link.filename in release.assets:
        return "OK", details
    return "MISSING", details


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="Markdown or text files to scan")
    parser.add_argument(
        "--candidate", action="append", type=parse_candidate, default=[],
        metavar="OWNER/REPO=RELEASE_ID",
        help="resolve latest links for this repo against a specific release, including drafts",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    candidates = dict(args.candidate)
    if len(candidates) != len(args.candidate):
        parser.error("give at most one candidate per repository")

    links = []
    for file in args.files:
        try:
            links.extend(find_links(file, file.read_text(encoding="utf-8-sig")))
        except (OSError, UnicodeError) as error:
            print(f"ERROR {file}: {error}", file=sys.stderr)
            return 2
    if not links:
        print("ERROR No supported GitHub release download URLs found.", file=sys.stderr)
        return 2

    latest_repos = {link.repo.lower() for link in links if link.tag is None}
    unused = candidates.keys() - latest_repos
    if unused:
        print(f"ERROR No latest download links for {', '.join(sorted(unused))}.", file=sys.stderr)
        return 2

    github = GitHub(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    missing = errors = 0
    for link in links:
        status, message = validate(link, github, candidates)
        print(f"{link.file}:{link.line}: {status} {message}")
        missing += status == "MISSING"
        errors += status == "ERROR"
    passed = len(links) - missing - errors
    print(f"{len(links)} link(s): {passed} OK, {missing} missing, {errors} error(s)")
    return 2 if errors else int(missing > 0)


if __name__ == "__main__":
    raise SystemExit(main())
