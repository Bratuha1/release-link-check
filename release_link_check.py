#!/usr/bin/env python3
"""Check GitHub release download URLs found in local Markdown and text files."""

import argparse
from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


VERSION = "0.3.0"
API_HOST = "api.github.com"
REPO_PATTERN = r"[A-Za-z0-9-]+/(?!\.{1,2}(?:/|$))[A-Za-z0-9_.-]+"
DOWNLOAD_URL = re.compile(
    rf"""https://github\.com/(?P<repo>{REPO_PATTERN})/releases/"""
    r"""(?P<route>latest/download|download)/(?P<path>[^\s\x00-\x1f<>()\[\]{}"'\x60?#]+)"""
)
SHELL_VARIABLE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")


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


@dataclass(frozen=True)
class LocalRelease:
    repo: str
    tag: str | None
    directory: Path

    def matches(self, link):
        return link.repo.lower() == self.repo and (link.tag is None or link.tag == self.tag)


def find_links(file, text):
    for number, line in enumerate(text.splitlines(), 1):
        for match in DOWNLOAD_URL.finditer(line):
            path = match["path"]
            if SHELL_VARIABLE.search(path) or (
                path.endswith("$") and line[match.end():match.end() + 1] in ("{", "(")
            ):
                continue
            path = path.rstrip(".,;:!")
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


class ReleaseNotFound(CheckError):
    """GitHub returned HTTP 404."""


class GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        destination = urlsplit(newurl)
        if destination.scheme != "https" or destination.netloc != API_HOST:
            raise CheckError("GitHub API returned a redirect outside api.github.com")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHub:
    def __init__(self, token=None):
        self.opener = build_opener(GitHubRedirect())
        self.token = token.strip() if token else ""
        self.headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"release-link-check/{VERSION}",
        }
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"
        self.cache = {}

    def get(self, path):
        request = Request(f"https://{API_HOST}{path}", headers=self.headers)
        try:
            with self.opener.open(request, timeout=15) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code == 404:
                raise ReleaseNotFound("release not found or not accessible (HTTP 404)") from None
            if error.code == 429 or (error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0"):
                raise CheckError("GitHub API rate limit reached") from None
            raise CheckError(f"GitHub API returned HTTP {error.code}") from None
        except (OSError, URLError, ValueError) as error:
            raise CheckError(f"could not read the GitHub API response: {error}") from None

    def release(self, repo, selector, *, include_drafts=False):
        name = repo.lower()
        key = name, selector, include_drafts
        if key not in self.cache:
            try:
                self.cache[key] = self.fetch_release(name, selector, include_drafts)
            except CheckError as error:
                self.cache[key] = error
        result = self.cache[key]
        if isinstance(result, CheckError):
            raise result
        return result

    def fetch_release(self, repo, selector, include_drafts):
        base = f"/repos/{repo}/releases"
        try:
            data = self.get(f"{base}/{selector}")
        except ReleaseNotFound:
            if not include_drafts:
                raise
            if not self.token:
                raise CheckError("tag not found. For drafts, set GH_TOKEN or GITHUB_TOKEN with draft access") from None
            data = self.find_draft(base, unquote(selector.removeprefix("tags/")))
        if (
            not isinstance(data, dict)
            or type(data.get("id")) is not int
            or not isinstance(data.get("tag_name"), str)
        ):
            raise CheckError("GitHub API returned invalid release metadata")
        assets = set()
        for asset in self.list_releases_or_assets(f"{base}/{data['id']}/assets"):
            if not isinstance(asset.get("name"), str):
                raise CheckError("GitHub API returned invalid asset metadata")
            if asset.get("state") == "uploaded":
                assets.add(asset["name"])
        return Release(data["tag_name"], frozenset(assets))

    def find_draft(self, base, tag):
        matches = [
            release for release in self.list_releases_or_assets(base)
            if release.get("draft") and release.get("tag_name") == tag
        ]
        if not matches:
            raise CheckError("no visible release with that tag. Check the tag and token's draft access")
        if len(matches) > 1:
            raise CheckError("multiple drafts have that tag. Select one with --candidate OWNER/REPO=RELEASE_ID")
        return matches[0]

    def list_releases_or_assets(self, path):
        page = 1
        while True:
            batch = self.get(f"{path}?per_page=100&page={page}")
            if not isinstance(batch, list) or any(not isinstance(item, dict) for item in batch):
                raise CheckError("GitHub API returned invalid list metadata")
            yield from batch
            if len(batch) < 100:
                return
            page += 1


def parse_candidate(value):
    repo, separator, release_id = value.partition("=")
    if not separator or not re.fullmatch(REPO_PATTERN, repo) or not re.fullmatch(r"[1-9][0-9]*", release_id):
        raise argparse.ArgumentTypeError("expected OWNER/REPO=RELEASE_ID")
    return repo.lower(), release_id


def parse_candidate_tag(value):
    repo, separator, tag = value.partition("=")
    if not separator:
        repo, tag = None, repo
    elif not re.fullmatch(REPO_PATTERN, repo):
        raise argparse.ArgumentTypeError("expected TAG or OWNER/REPO=TAG")
    if not tag or tag in {".", ".."} or any(char.isspace() or ord(char) < 32 for char in tag):
        raise argparse.ArgumentTypeError("expected a valid release tag")
    return repo.lower() if repo else None, tag


def parse_local_release(value):
    target, equals, directory = value.partition("=")
    repo, at, tag = target.partition("@")
    if not equals or not directory or not re.fullmatch(REPO_PATTERN, repo):
        raise argparse.ArgumentTypeError("expected OWNER/REPO[@TAG]=DIRECTORY")
    if at:
        _, tag = parse_candidate_tag(tag)
    return LocalRelease(repo.lower(), tag or None, Path(directory))


def suggested_names(filename, assets):
    expected = filename.casefold()
    case_matches = sorted(asset for asset in assets if asset.casefold() == expected)
    if case_matches:
        return case_matches[:3]
    suffix = Path(filename).suffix.casefold()
    scores = [
        (SequenceMatcher(None, expected, asset.casefold(), autojunk=False).ratio(), asset)
        for asset in assets if Path(asset).suffix.casefold() == suffix
    ]
    return [asset for score, asset in sorted(scores, key=lambda item: (-item[0], item[1]))[:3] if score >= 0.65]


def validate(link, github, candidates, candidate_tags=None):
    target = f"tags/{quote(link.tag, safe='')}" if link.tag is not None else "latest"
    repo = link.repo.lower()
    candidate_id = candidates.get(repo) if link.tag is None else None
    candidate_tag = (candidate_tags or {}).get(repo) if link.tag is None else None
    context = f"tag {ascii(link.tag)}" if link.tag is not None else "latest release"
    try:
        if candidate_tag is not None:
            context = f"candidate tag {ascii(candidate_tag)}"
            release = github.release(link.repo, f"tags/{quote(candidate_tag, safe='')}", include_drafts=True)
        else:
            if candidate_id:
                context = f"candidate release #{candidate_id}"
            release = github.release(link.repo, candidate_id or target)
    except CheckError as error:
        return "ERROR", f"{link.repo} {context}: {error}"
    return check_asset(link.filename, release.assets, f"{link.repo} {ascii(release.tag)}")


def check_asset(filename, assets, location, *, local=False):
    details = f"{ascii(filename)} in {location}"
    if filename in assets:
        return "OK", details
    names = suggested_names(filename, assets)
    if names:
        source = "local" if local else "uploaded"
        details += f". Similar {source} names: {', '.join(map(ascii, names))}"
    elif not assets:
        details += ". No files found in the local folder" if local else ". This release has no uploaded files"
    elif not local:
        details += ". Check the release for the exact asset name"
    return "MISSING", details


def annotate(link, status, message):
    def escape(value):
        return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    file = escape(link.file.as_posix()).replace(":", "%3A").replace(",", "%2C")
    print(f"::error file={file},line={link.line},title=Release link {status.lower()}::{escape(message)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files", nargs="*", type=Path, metavar="FILE",
        help="Markdown or text files to scan (default: README.md)",
    )
    parser.add_argument(
        "--candidate", action="append", type=parse_candidate, default=[],
        metavar="OWNER/REPO=RELEASE_ID",
        help="resolve latest links for this repo against a specific release, including drafts",
    )
    parser.add_argument(
        "--candidate-tag", action="append", type=parse_candidate_tag, default=[],
        metavar="[OWNER/REPO=]TAG",
        help="resolve latest links against a release or visible draft with this tag",
    )
    parser.add_argument(
        "--local-release", type=parse_local_release, metavar="OWNER/REPO[@TAG]=DIRECTORY",
        help="check intended download links against the files in a local release folder, without network access",
    )
    parser.add_argument("--github-annotations", action="store_true", help="annotate bad links in GitHub Actions")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    if args.local_release and (args.candidate or args.candidate_tag):
        parser.error("--local-release cannot be combined with --candidate or --candidate-tag")
    candidates = dict(args.candidate)
    if len(candidates) != len(args.candidate):
        parser.error("give at most one candidate per repository")

    links = []
    for file in args.files or [Path("README.md")]:
        try:
            links.extend(find_links(file, file.read_text(encoding="utf-8-sig")))
        except (OSError, UnicodeError) as error:
            print(f"ERROR {file}: {error}", file=sys.stderr)
            return 2
    if not links:
        print("ERROR No supported GitHub release download URLs found. Use literal URLs, without shell variables.", file=sys.stderr)
        return 2
    if args.local_release:
        return check_local_release(links, args.local_release, args.github_annotations)

    latest_repos = {link.repo.lower() for link in links if link.tag is None}
    candidate_tags = {}
    for repo, tag in args.candidate_tag:
        if repo is None:
            if len(latest_repos) != 1:
                parser.error("use --candidate-tag OWNER/REPO=TAG unless all latest links point to one repository")
            repo = next(iter(latest_repos))
        if repo in candidates or repo in candidate_tags:
            parser.error("give at most one candidate per repository")
        candidate_tags[repo] = tag
    unused = (candidates.keys() | candidate_tags.keys()) - latest_repos
    if unused:
        print(f"ERROR No latest download links for {', '.join(sorted(unused))}.", file=sys.stderr)
        return 2

    github = GitHub(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    return print_results(
        ((link, validate(link, github, candidates, candidate_tags)) for link in links),
        args.github_annotations,
    )


def check_local_release(links, local_release, annotations):
    selected = [link for link in links if local_release.matches(link)]
    if not selected:
        print(
            f"ERROR No links match the local release for {local_release.repo}. "
            "Use OWNER/REPO@TAG for links pinned to a tag.",
            file=sys.stderr,
        )
        return 2
    directory = local_release.directory
    try:
        assets = {entry.name for entry in directory.iterdir() if entry.is_file()}
    except OSError as error:
        print(f"ERROR Could not read local release folder {directory}: {error}", file=sys.stderr)
        return 2
    location = f"local folder {ascii(directory.as_posix())}"
    return print_results(
        ((link, check_asset(link.filename, assets, location, local=True)) for link in selected),
        annotations,
        skipped=len(links) - len(selected),
    )


def print_results(results, annotations, *, skipped=0):
    passed = 0
    missing = errors = 0
    for link, (status, message) in results:
        print(f"{link.file}:{link.line}: {status} {message}")
        if annotations and status != "OK":
            annotate(link, status, message)
        passed += status == "OK"
        missing += status == "MISSING"
        errors += status == "ERROR"
    checked = passed + missing + errors
    summary = f"{checked} link(s): {passed} OK, {missing} missing, {errors} error(s)"
    if skipped:
        summary += f", {skipped} outside local release"
    print(summary)
    return 2 if errors else int(missing > 0)


if __name__ == "__main__":
    raise SystemExit(main())
