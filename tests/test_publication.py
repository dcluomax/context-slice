from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from scripts import audit_publication


class PublicationTests(unittest.TestCase):
    def audit(self, author, committer_name, committer, blob=None):
        revision = "a" * 40

        def fake_git(*args):
            if args[0] == "log":
                fields = [revision, author]
                if "%cn" in args[-1]:
                    fields.append(committer_name)
                fields.append(committer)
                return ("\0".join(fields) + "\n").encode()
            if args[0] == "rev-list":
                return b"b fixture.env\n" if blob is not None else b""
            if args[:2] == ("cat-file", "-t"):
                return b"blob\n"
            if args[:2] == ("cat-file", "blob"):
                return blob
            raise AssertionError(args)

        output = io.StringIO()
        with patch.object(audit_publication, "git", side_effect=fake_git):
            with redirect_stdout(output):
                result = audit_publication.main()
        return result, json.loads(output.getvalue())["findings"]

    def test_github_web_committer_is_platform_metadata(self):
        result, findings = self.audit(
            "1477354+dcluomax@users.noreply.github.com", "GitHub", "noreply@github.com"
        )
        self.assertEqual(result, 0)
        self.assertEqual(findings, [])

    def test_platform_email_is_not_an_author_identity(self):
        result, findings = self.audit("noreply@github.com", "GitHub", "noreply@github.com")
        self.assertEqual(result, 1)
        self.assertEqual([finding["field"] for finding in findings], ["author"])

    def test_platform_committer_requires_exact_name_and_email(self):
        for name, email in (
            ("github", "noreply@github.com"),
            ("GitHub Actions", "noreply@github.com"),
            ("Other", "noreply@github.com"),
            ("GitHub", "NOREPLY@github.com"),
            ("GitHub", "noreply@github.com.example.invalid"),
            ("GitHub", "noreply@example.invalid"),
        ):
            with self.subTest(name=name, email=email):
                result, findings = self.audit(
                    "1477354+dcluomax@users.noreply.github.com", name, email
                )
                self.assertEqual(result, 1)
                self.assertEqual([finding["field"] for finding in findings], ["committer"])

    def test_platform_committer_does_not_skip_content_checks(self):
        result, findings = self.audit(
            "1477354+dcluomax@users.noreply.github.com",
            "GitHub",
            "noreply@github.com",
            blob=b"ghp_" + b"a" * 36,
        )
        self.assertEqual(result, 1)
        self.assertEqual(
            {finding["kind"] for finding in findings},
            {"credential_pattern", "runtime_state_or_environment_file"},
        )


if __name__ == "__main__":
    unittest.main()
