import fnmatch
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_PREFIXES = ('data/private/', 'outputs/private/')
PRIVATE_GLOBS = ('private-kit-*',)


def offending(paths):
    """Tracked paths that belong to the private, never-committed areas."""
    bad = []
    for p in paths:
        parts = p.split('/')
        if p.startswith(PRIVATE_PREFIXES) or any(
                fnmatch.fnmatch(part, g) for part in parts for g in PRIVATE_GLOBS):
            bad.append(p)
    return bad


class PrivatePathsUntrackedTests(unittest.TestCase):
    def test_guard_flags_fake_tracked_paths(self):
        fake = ['README.md', 'data/private/kits/manifest.csv', 'outputs/private/x.json',
                'a/private-kit-notes.txt', 'private-kit-x/y', 'data/openmic-2018/readme']
        self.assertEqual(offending(fake), fake[1:5])

    def test_nothing_private_is_tracked(self):
        try:
            out = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True,
                                 check=True).stdout.splitlines()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest('git checkout not available')
        self.assertEqual(offending(out), [])

    def test_private_locations_are_gitignored(self):
        for p in ('data/private/kits/x', 'outputs/private/x', 'outputs/drum-masking/x'):
            try:
                rc = subprocess.run(['git', 'check-ignore', '-q', p], cwd=ROOT).returncode
            except OSError:
                self.skipTest('git not available')
            self.assertEqual(rc, 0, f'{p} is not ignored')


if __name__ == '__main__':
    unittest.main()
