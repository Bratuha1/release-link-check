import contextlib
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch
from urllib.request import Request

from release_link_check import (
    CheckError, GitHub, GitHubRedirect, Link, Release, ReleaseNotFound,
    annotate, find_links, main, validate,
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

    def test_shell_variables_do_not_become_truncated_download_urls(self):
        text = "\n".join([
            'curl "https://github.com/Owner/Tool/releases/download/$VERSION/App.zip"',
            'curl "https://github.com/Owner/Tool/releases/latest/download/App-${ARCH}.zip"',
            'curl "https://github.com/Owner/Tool/releases/latest/download/App-$(uname -m).zip"',
            'curl "https://github.com/Owner/Tool/releases/latest/download/App%24special.zip"',
        ])
        self.assertEqual(list(find_links(Path("README.md"), text)), [
            Link(Path("README.md"), 4, "Owner/Tool", None, "App$special.zip"),
        ])

    def test_missing_link_suggests_exact_case_or_similar_asset(self):
        github = Mock()
        github.release.return_value = Release("v2", frozenset({
            "App-Windows-v2.zip", "App-Linux-v2.zip", "different.zip",
        }))
        file = Path("README.md")
        wrong_case = Link(file, 1, "Owner/Tool", None, "app-windows-v2.zip")
        old_name = Link(file, 2, "Owner/Tool", None, "App-Windows-v1.zip")
        status, message = validate(wrong_case, github, {})
        self.assertEqual(status, "MISSING")
        self.assertTrue(message.endswith("Similar uploaded names: 'App-Windows-v2.zip'"))
        status, message = validate(old_name, github, {})
        self.assertEqual(status, "MISSING")
        self.assertIn("'App-Windows-v2.zip'", message)
        self.assertNotIn("different.zip", message)

    def test_annotation_escapes_filename_and_message(self):
        file = Path("docs") / "one,100%.md"
        link = Link(file, 3, "Owner/Tool", None, "App.zip")
        output = StringIO()
        with contextlib.redirect_stdout(output):
            annotate(link, "MISSING", "Title\nwith %")
        self.assertEqual(
            output.getvalue(),
            "::error file=docs/one%2C100%25.md,line=3,title=Release link missing::Title%0Awith %25\n",
        )


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

    def test_draft_lookup_does_not_leak_into_pinned_lookup_cache(self):
        github = GitHub("token")
        github.get = Mock(side_effect=[
            ReleaseNotFound(),
            [{"id": 123, "tag_name": "draft/v2", "draft": True}],
            [{"name": "app.zip", "state": "uploaded"}],
            ReleaseNotFound(),
        ])
        result = github.release("Owner/Tool", "tags/draft%2Fv2", include_drafts=True)
        self.assertEqual(result, Release("draft/v2", frozenset({"app.zip"})))
        with self.assertRaises(ReleaseNotFound):
            github.release("Owner/Tool", "tags/draft%2Fv2")
        self.assertEqual(github.get.call_args_list, [
            call("/repos/owner/tool/releases/tags/draft%2Fv2"),
            call("/repos/owner/tool/releases?per_page=100&page=1"),
            call("/repos/owner/tool/releases/123/assets?per_page=100&page=1"),
            call("/repos/owner/tool/releases/tags/draft%2Fv2"),
        ])

    def test_missing_draft_tag_tells_unauthenticated_user_how_to_continue(self):
        github = GitHub()
        github.get = Mock(side_effect=ReleaseNotFound())
        with self.assertRaisesRegex(CheckError, "set GH_TOKEN or GITHUB_TOKEN"):
            github.release("Owner/Tool", "tags/draft", include_drafts=True)
        github.get.assert_called_once()


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

    def test_default_readme_and_bare_candidate_tag(self):
        cwd = Path.cwd()
        with TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                Path("README.md").write_text(
                    "https://github.com/Owner/Tool/releases/latest/download/App.zip",
                    encoding="utf-8",
                )
                with patch("release_link_check.GitHub") as github, contextlib.redirect_stdout(StringIO()):
                    github.return_value.release.return_value = Release("v2", frozenset({"App.zip"}))
                    self.assertEqual(main(["--candidate-tag", "v2"]), 0)
                github.return_value.release.assert_called_once_with("Owner/Tool", "tags/v2", include_drafts=True)
            finally:
                os.chdir(cwd)

    def test_bare_candidate_tag_requires_single_latest_repo(self):
        text = "\n".join([
            "https://github.com/Owner/Tool/releases/latest/download/App.zip",
            "https://github.com/Else/Tool/releases/latest/download/App.zip",
        ])
        with self.assertRaises(SystemExit) as error:
            self.run_cli(text, None, ["--candidate-tag", "v2"])
        self.assertEqual(error.exception.code, 2)

    def test_local_release_checks_latest_and_selected_tag_without_api(self):
        with TemporaryDirectory() as folder:
            dist = Path(folder) / "build output=windows"
            dist.mkdir()
            (dist / "App.zip").touch()
            (dist / "Docs.zip").mkdir()
            (dist / "Docs.zip" / "Docs.zip").touch()
            readme = Path(folder) / "README.md"
            readme.write_text("\n".join([
                "https://github.com/Owner/Tool/releases/latest/download/App.zip",
                "https://github.com/Owner/Tool/releases/download/next/v2/App.zip",
                "https://github.com/Owner/Tool/releases/download/v1/App.zip",
                "https://github.com/Another/Tool/releases/latest/download/App.zip",
                "https://github.com/Owner/Tool/releases/latest/download/Docs.zip",
                "https://github.com/Owner/Tool/releases/latest/download/app.zip",
            ]), encoding="utf-8")
            output = StringIO()
            with patch("release_link_check.GitHub") as github, contextlib.redirect_stdout(output):
                code = main(["--local-release", f"owner/tool@next/v2={dist}", str(readme)])
            github.assert_not_called()
            self.assertEqual(code, 1)
            report = output.getvalue()
            self.assertIn(":1: OK", report)
            self.assertIn(":2: OK", report)
            self.assertNotIn(":3:", report)
            self.assertNotIn(":4:", report)
            self.assertIn(":5: MISSING", report)
            self.assertIn("Similar local names: 'App.zip'", report)
            self.assertIn("4 link(s): 2 OK, 2 missing, 0 error(s), 2 outside local release", report)

    def test_local_release_no_matching_links_or_missing_folder(self):
        with TemporaryDirectory() as folder:
            readme = Path(folder) / "README.md"
            readme.write_text(
                "https://github.com/Owner/Tool/releases/download/v1/App.zip",
                encoding="utf-8",
            )
            output = StringIO()
            with contextlib.redirect_stderr(output):
                self.assertEqual(main([str(readme), "--local-release", f"Owner/Tool={folder}"]), 2)
                self.assertEqual(main([str(readme), "--local-release", f"Owner/Tool@v1={folder}/missing"]), 2)
            self.assertIn("No links match the local release", output.getvalue())
            self.assertIn("Could not read local release folder", output.getvalue())


if __name__ == "__main__":
    unittest.main()
