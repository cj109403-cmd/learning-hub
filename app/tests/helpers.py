"""Every integration test supplies a temporary project; never copy the project root."""
import os
import shutil
import tempfile
import errno
import sys
from pathlib import Path
from contextlib import contextmanager

REPO = Path(__file__).resolve().parents[2]
# Tests run against the project's own course by default. Set LEARNING_HUB_TEST_COURSE to a
# folder with content/ and data/ (for example app/tests/fixtures/demo-course) to run them
# against another course; no test may depend on one particular course.
COURSE = Path(os.environ.get('LEARNING_HUB_TEST_COURSE') or REPO).resolve()
DEMO_COURSE = REPO / 'app' / 'tests' / 'fixtures' / 'demo-course'


def symlink_or_skip(test, link, target, *, directory=False):
    """Exercise real links where supported; ordinary Windows users may lack it.

    This never enables Developer Mode or requests administrator rights. Junction
    rejection is also covered by separate simulated reparse-point tests.
    """
    try:
        Path(link).symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as error:
        permission_missing = (isinstance(error, NotImplementedError) or
                              getattr(error, "winerror", None) == 1314 or
                              getattr(error, "errno", None) in (errno.EACCES, errno.EPERM))
        if sys.platform == "win32" and permission_missing:
            test.skipTest("当前 Windows 用户不能创建符号链接；未启用开发者模式或管理员权限")
        raise


@contextmanager
def temporary_project(static=False):
    with tempfile.TemporaryDirectory(prefix='learning-hub-test space-') as directory:
        root = Path(directory).resolve()
        for name in ('content', 'data'):
            shutil.copytree(COURSE / name, root / name)
        if static:
            for name in ('static', 'vendor'):
                shutil.copytree(REPO / 'app' / name, root / 'app' / name)
        yield root
