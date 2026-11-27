import tempfile
import unittest
from pathlib import Path

from ohrin import paths


class DataDirTests(unittest.TestCase):
    home = Path("/home/u")

    def test_data_dir_linux_xdg_absolute_and_relative(self):
        share = Path(tempfile.gettempdir()) / "share"  # absolute on every host OS
        got = paths.user_data_dir(env={"XDG_DATA_HOME": str(share)}, platform="linux", home=self.home)
        self.assertEqual(got, share / "Ohrin")
        got = paths.user_data_dir(env={"XDG_DATA_HOME": "relative/dir"}, platform="linux", home=self.home)
        self.assertEqual(got, self.home / ".local/share/Ohrin")
        got = paths.user_data_dir(env={}, platform="linux", home=self.home)
        self.assertEqual(got, self.home / ".local/share/Ohrin")

    def test_data_dir_macos(self):
        got = paths.user_data_dir(env={}, platform="darwin", home=self.home)
        self.assertEqual(got, self.home / "Library/Application Support/Ohrin")

    def test_data_dir_windows_localappdata_and_fallback(self):
        got = paths.user_data_dir(env={"LOCALAPPDATA": "/w/Local"}, platform="win32", home=self.home)
        self.assertEqual(got, Path("/w/Local") / "Ohrin")
        got = paths.user_data_dir(env={}, platform="win32", home=self.home)
        self.assertEqual(got, self.home / "AppData/Local/Ohrin")

    def test_data_dir_env_override_wins(self):
        env = {"OHRIN_DATA_DIR": "/custom", "XDG_DATA_HOME": "/x", "LOCALAPPDATA": "/w"}
        for platform in ("linux", "darwin", "win32"):
            self.assertEqual(paths.user_data_dir(env=env, platform=platform, home=self.home), Path("/custom"))

    def test_subdirs_and_nothing_created(self):
        env = {"OHRIN_DATA_DIR": "/nonexistent-ohrin-test/data"}
        from unittest import mock
        with mock.patch.dict("os.environ", env, clear=False):
            self.assertEqual(paths.vggish_dir(), Path("/nonexistent-ohrin-test/data/vggish"))
            self.assertEqual(paths.models_dir(), Path("/nonexistent-ohrin-test/data/models"))
            self.assertEqual(paths.webview_storage_dir(), Path("/nonexistent-ohrin-test/data/webview"))
            self.assertEqual(paths.log_dir(), Path("/nonexistent-ohrin-test/data/logs"))
        self.assertFalse(Path("/nonexistent-ohrin-test").exists())

    def test_resource_root_frozen_and_source(self):
        self.assertEqual(paths.resource_root(frozen=True, meipass="/bundle/_internal"), Path("/bundle/_internal"))
        repo = Path(paths.__file__).resolve().parent.parent
        self.assertEqual(paths.resource_root(frozen=False), repo)
        self.assertTrue((repo / "web").is_dir())


if __name__ == "__main__":
    unittest.main()
