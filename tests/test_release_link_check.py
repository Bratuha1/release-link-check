import contextlib
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch
from urllib.request import Request

from release_link_check import (
    CheckError, GitHub, GitHubRedirect, Link, Release, find_links, main, validate,
)


class LinkTests(unittest.TestCase):
    def test_markdown_code_block_and_encoded_paths(self):
        file = Path("README.md")
        lines = [
            "[Download](https://github.com/Owner/Tool/releases/latest/download/An%20App.zip)",
            "```sh",
            "curl 'https://github.com/Owner/Tool/releases/download/builds/v2/My%28App%29.zip?x=1'",
            "```",
            "Try https://github.com/Owner/Tool/releases/latest/download/App.zip.",
            "Visit https://github.com/Owner/Tool/releases/tag/v2",
            "Other https://example.org/Owner/Tool/releases/latest/download/App.zip",
        ]
        self.assertEqual(list(find_links(file, "\n".join(lines))), [
            Link(file, 1, "Owner/Tool", None, "An App.zip"),
            Link(file, 3, "Owner/Tool", "builds/v2", "My(App).zip"),
            Link(file, 5, "Owner/Tool", None, "App.zip"),
        ])

    def test_candidate_changes_latest_only(self):
        github = Mock()
        github.release.return_value = Release("v2", frozenset({"App.zip"}))
        file = Path("README.md")
        candidates = {"owner/tool": "123"}
        latest = Link(file, 1, "Owner/Tool", None, "App.zip")
        pinned = Link(file, 2, "Owner/Tool", "builds/v1", "app.zip")
        self.assertEqual(validate(latest, github, candidates)[0], "OK")
        self.assertEqual(validate(pinned, github, candidates)[0], "MISSING")
        self.assertEqual(github.release.call_args_list, [
            call("Owner/Tool", "123"), call("Owner/Tool", "tags/builds%2Fv1"),
        ])


class GitHubTests(unittest.TestCase):
    def test_pagination_state_and_cache(self):
        github = GitHub()
        page_one = [{"name": f"{i}.zip", "state": "uploaded"} for i in range(100)]
        page_two = [{"name": "ready.zip", "state": "uploaded"}, {"name": "placeholder.zip", "state": "starter"}]
        github.get = Mock(side_effect=[{"id": 123, "tag_name": "v2"}, page_one, page_two])
        result = github.release("Owner/Tool", "latest")
        self.assertEqual(len(result.assets), 101)
        self.assertIn("ready.zip", result.assets)
        self.assertNotIn("placeholder.zip", result.assets)
        self.assertIs(github.release("owner/tool", "latest"), result)
        self.assertEqual(github.get.call_args_list, [
            call("/repos/owner/tool/releases/latest"),
            call("/repos/owner/tool/releases/123/assets?per_page=100&page=1"),
            call("/repos/owner/tool/releases/123/assets?per_page=100&page=2"),
        ])

    def test_blocks_redirect_to_another_host(self):
        request = Request("https://api.github.com/repos/owner/tool/releases/latest")
        with self.assertRaises(CheckError):
            GitHubRedirect().redirect_request(request, None, 302, "", {}, "https://example.org")


class CliTests(unittest.TestCase):
    def run_cli(self, text, release, extra_args=()):
        with TemporaryDirectory() as folder:
            file = Path(folder) / "install guide.md"
            file.write_text(text, encoding="utf-8-sig")
            output = StringIO()
            with patch("release_link_check.GitHub") as github, contextlib.redirect_stdout(output), \
                    contextlib.redirect_stderr(output):
                if isinstance(release, Exception):
                    github.return_value.release.side_effect = release
                else:
                    github.return_value.release.return_value = release
                code = main([str(file), *extra_args])
            return code, output.getvalue()

    def test_exit_codes_and_line_number(self):
        text = "# Downloads\nhttps://github.com/owner/tool/releases/latest/download/App.zip"
        cases = [
            (Release("v1", frozenset({"App.zip"})), 0, ":2: OK"),
            (Release("v1", frozenset()), 1, ":2: MISSING"),
            (CheckError("API unavailable"), 2, ":2: ERROR"),
        ]
        for release, exit_status, diagnostic in cases:
            with self.subTest(exit_status=exit_status):
                code, output = self.run_cli(text, release)
                self.assertEqual(code, exit_status)
                self.assertIn(diagnostic, output)

    def test_no_links_and_unused_candidate_fail(self):
        code, output = self.run_cli("No links here", None)
        self.assertEqual(code, 2)
        self.assertIn("No supported", output)
        code, output = self.run_cli(
            "https://github.com/owner/tool/releases/download/v1/App.zip",
            None,
            ["--candidate", "owner/tool=123"],
        )
        self.assertEqual(code, 2)
        self.assertIn("No latest download links", output)


if __name__ == "__main__":
    unittest.main()
