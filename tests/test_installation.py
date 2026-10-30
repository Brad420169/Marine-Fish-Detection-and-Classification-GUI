"""Installer control-flow checks using fake platform tools; no downloads or desktop changes."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('bash'), 'requires bash')
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='marine install ')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.bin = self.home / '.pixi/bin'
        self.bin.mkdir(parents=True)
        self.log = self.home / 'calls'
        self.env = dict(os.environ, HOME=str(self.home), PATH=f'{self.bin}:/usr/bin:/bin',
                        INSTALL_DIR=str(self.home / 'Apps with spaces'), CALL_LOG=str(self.log),
                        TEST_OS='Darwin', TEST_ARCH='arm64', TEST_VERSION='13.0')
        self.tool('uname', 'if [[ "$1" == -s ]]; then echo "$TEST_OS"; else echo "$TEST_ARCH"; fi')
        self.tool('sw_vers', 'echo "$TEST_VERSION"')
        self.tool('curl', '''echo "curl $*" >> "$CALL_LOG"
if [[ "${FAIL_STAGE:-}" == model ]]; then exit 7; fi
printf 'model fixture' > "$6"''')
        self.tool('sha256sum', '''if [[ "${FAIL_STAGE:-}" == checksum ]]; then
    echo 'incorrect'
else
    echo '0b259afb3dca1d1d7f9d842ebf136884b2e3706b151172b266bb24e07f2b8e98'
fi''')
        self.tool('pixi', '''echo "pixi $*" >> "$CALL_LOG"
if [[ "$1" == global ]]; then
    [[ "${PIXI_EXIT:-0}" == 0 ]] || exit "$PIXI_EXIT"
    touch "$HOME/git-ready"
elif [[ "${FAIL_STAGE:-}" == environment ]]; then
    exit 7
fi''')
        self.tool('git', '''echo "git $*" >> "$CALL_LOG"
if [[ "${NEED_GIT:-0}" == 1 && ! -e "$HOME/git-ready" ]]; then exit 1; fi
if [[ "${FAIL_STAGE:-}" == clone && "$1" == clone ]]; then exit 7; fi
if [[ "${FAIL_STAGE:-}" == lfs && "$*" == 'lfs pull' ]]; then exit 7; fi
if [[ "$1" == clone ]]; then
    mkdir -p "$3/setup"
    for platform in linux macos; do
        printf 'echo shortcut-%s >> "$CALL_LOG"\n' "$platform" > "$3/setup/create_${platform}_shortcut.sh"
    done
    if [[ "${FAIL_STAGE:-}" == shortcut ]]; then
        printf 'exit 7\n' > "$3/setup/create_macos_shortcut.sh"
    fi
fi''')

    def tool(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/bash\nset -eu\n' + body + '\n')
        path.chmod(0o755)

    def run_script(self, path):
        return subprocess.run(['bash', str(path)], env=self.env, text=True, capture_output=True, cwd=self.home)

    def test_supported_platforms(self):
        for system, arch, shortcut in [('Darwin', 'arm64', 'macos'), ('Linux', 'x86_64', 'linux')]:
            with self.subTest(system=system):
                self.env.update(TEST_OS=system, TEST_ARCH=arch, INSTALL_DIR=str(self.home / system))
                result = self.run_script(ROOT / 'setup/installation.sh')
                self.assertEqual(result.returncode, 0, result.stderr)
                model = Path(self.env['INSTALL_DIR']) / ROOT.name / 'models/Fish_Only_YOLO12.pt'
                self.assertEqual(model.read_text(), 'model fixture')
                self.assertIn('Installation complete:', result.stdout)
                self.assertIn('You can now open', result.stdout)
                self.assertLess(result.stdout.index('Pixi environment installed.'),
                                result.stdout.index('Desktop shortcut done.'))
                calls = self.log.read_text()
                self.assertIn('pixi install --locked', calls)
                self.assertIn(f'shortcut-{shortcut}', calls)
                self.log.unlink()

    def test_unsupported_macs_stop_before_download(self):
        for arch, version in [('x86_64', '13.0'), ('arm64', '12.7')]:
            with self.subTest(arch=arch, version=version):
                self.env.update(TEST_ARCH=arch, TEST_VERSION=version)
                self.assertNotEqual(self.run_script(ROOT / 'setup/installation.sh').returncode, 0)
                self.assertFalse(self.log.exists())

    def test_existing_install_is_preserved(self):
        project = Path(self.env['INSTALL_DIR']) / ROOT.name
        project.mkdir(parents=True)
        marker = project / 'keep.txt'
        marker.write_text('keep')
        self.assertNotEqual(self.run_script(ROOT / 'setup/installation.sh').returncode, 0)
        self.assertEqual(marker.read_text(), 'keep')
        self.assertFalse(self.log.exists())

    def test_failed_tool_install_stops_before_clone(self):
        self.env.update(PIXI_EXIT='1', NEED_GIT='1')
        self.assertNotEqual(self.run_script(ROOT / 'setup/installation.sh').returncode, 0)
        self.assertNotIn('git clone', self.log.read_text())

    def test_existing_tools_skip_install(self):
        result = self.run_script(ROOT / 'setup/installation.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already installed', result.stdout)
        self.assertNotIn('pixi global install', self.log.read_text())

    def test_missing_tools_are_installed(self):
        self.env['NEED_GIT'] = '1'
        result = self.run_script(ROOT / 'setup/installation.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Git and Git LFS installed successfully.', result.stdout)
        self.assertIn('pixi global install git git-lfs', self.log.read_text())

    def test_failures_report_step_without_success_message(self):
        for stage, message in [('clone', 'Cloning Git repository'),
                               ('lfs', 'Downloading model files'),
                               ('model', 'Downloading Community Fish Detector'),
                               ('environment', 'Installing Pixi environment'),
                               ('shortcut', 'Setting up desktop shortcut')]:
            with self.subTest(stage=stage):
                self.env.update(FAIL_STAGE=stage, INSTALL_DIR=str(self.home / stage))
                result = self.run_script(ROOT / 'setup/installation.sh')
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertIn('Installation failed during: ' + message, result.stderr)
                self.assertNotIn('Installation complete:', result.stdout)
                self.assertNotIn('You can now open', result.stdout)

    def test_bad_model_checksum_stops_and_cleans_download(self):
        self.env['FAIL_STAGE'] = 'checksum'
        result = self.run_script(ROOT / 'setup/installation.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Checksum mismatch', result.stderr)
        models = Path(self.env['INSTALL_DIR']) / ROOT.name / 'models'
        self.assertEqual(list(models.iterdir()), [])
        self.assertNotIn('pixi install', self.log.read_text())

    def test_relative_install_directory(self):
        self.env['INSTALL_DIR'] = 'relative apps'
        result = self.run_script(ROOT / 'setup/installation.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        model = self.home / 'relative apps' / ROOT.name / 'models/Fish_Only_YOLO12.pt'
        self.assertTrue(model.is_file())
        self.assertEqual(len(list(self.home.rglob('Fish_Only_YOLO12.pt'))), 1)

    def test_wget_and_macos_checksum_fallback(self):
        # Only expose the tools this branch needs, hiding host curl and sha256sum.
        self.env['PATH'] = str(self.bin)
        for name in ('bash', 'mkdir', 'cut', 'mktemp', 'awk', 'ln', 'rm'):
            (self.bin / name).symlink_to(shutil.which(name))
        (self.bin / 'curl').unlink()
        (self.bin / 'sha256sum').rename(self.bin / 'shasum')
        self.tool('wget', '''echo "wget $*" >> "$CALL_LOG"
printf 'model fixture' > "${2#--output-document=}"''')
        result = self.run_script(ROOT / 'setup/installation.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('wget --tries=3', self.log.read_text())
        model = Path(self.env['INSTALL_DIR']) / ROOT.name / 'models/Fish_Only_YOLO12.pt'
        self.assertEqual(model.read_text(), 'model fixture')

    def test_existing_model_is_preserved(self):
        git = self.bin / 'git'
        git.write_text(git.read_text() + '''
if [[ "$1" == clone ]]; then
    mkdir -p "$3/models"
    printf 'existing model' > "$3/models/Fish_Only_YOLO12.pt"
fi
''')
        for stage, expected_status in [('', 0), ('checksum', 1)]:
            with self.subTest(stage=stage):
                self.env.update(FAIL_STAGE=stage, INSTALL_DIR=str(self.home / ('existing-' + stage)))
                result = self.run_script(ROOT / 'setup/installation.sh')
                self.assertEqual(result.returncode, expected_status, result.stderr)
                model = Path(self.env['INSTALL_DIR']) / ROOT.name / 'models/Fish_Only_YOLO12.pt'
                self.assertEqual(model.read_text(), 'existing model')
                self.assertNotIn('curl ', self.log.read_text())
                self.log.unlink()

    def test_macos_launcher_quotes_paths_and_runs_from_project(self):
        project = self.home / "Project's $fish directory"
        (project / 'setup').mkdir(parents=True)
        shutil.copy(ROOT / 'setup/create_macos_shortcut.sh', project / 'setup')
        python = project / '.pixi/envs/default/bin/python'
        python.parent.mkdir(parents=True)
        python.symlink_to(shutil.which('python3'))
        # macOS icon tools are unavailable on Linux; verify their invocation separately.
        self.tool('sips', 'echo "sips $*" >> "$CALL_LOG"')
        self.tool('iconutil', 'echo "iconutil $*" >> "$CALL_LOG"')
        result = self.run_script(project / 'setup/create_macos_shortcut.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        contents = self.home / 'Desktop/Marine Fish Detection GUI.app/Contents'
        with (contents / 'Info.plist').open('rb') as stream:
            self.assertEqual(plistlib.load(stream)['CFBundleExecutable'], 'launch')
        self.tool('pixi', 'printf "%s\\n" "$PWD" "$@" > "$CALL_LOG"')
        result = self.run_script(contents / 'MacOS/launch')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(),
                         [str(project), 'run', '--manifest-path', str(project / 'pixi.toml'), 'GUI'])


if __name__ == '__main__':
    unittest.main()
