"""Verify distribution contents with real ZIP creation and isolated fixtures."""
from zipfile import ZipFile
import re

import pytest

from build_parallel import INSTALLATION_GUIDE_EN_PDF, INSTALLATION_GUIDE_PDF, create_distribution_zip


def test_zip_excludes_cloudflared_even_when_stale_copies_remain(tmp_path):
    root = tmp_path / "project"
    dist = root / "dist"
    dist.mkdir(parents=True)
    (root / "CHANGELOG.md").write_text("## [3.4.1] - 2026-11-09\n", encoding="utf-8")
    retained = ["SKC_translation.exe", "SKC_setup.exe", INSTALLATION_GUIDE_PDF, INSTALLATION_GUIDE_EN_PDF, "licenses/cloudflared-LICENSE.txt", "licenses/README.md"]
    excluded = ["cloudflared.exe", "old/CloudFlared.EXE", "SKC_start.bat", "old/SKC_START.BAT", "README.md", "CHANGELOG.md"]
    for name in retained + excluded:
        path = dist / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")

    archive = create_distribution_zip(dist, root)
    assert archive is not None
    with ZipFile(archive) as package:
        assert package.testzip() is None
        assert set(package.namelist()) == set(retained)
    assert all((dist / name).is_file() for name in excluded)


@pytest.mark.parametrize("missing", ["SKC_setup.exe", "SKC_translation.exe", INSTALLATION_GUIDE_PDF, INSTALLATION_GUIDE_EN_PDF])
def test_zip_rejects_missing_required_executable(tmp_path, missing):
    for name in ("SKC_setup.exe", "SKC_translation.exe", INSTALLATION_GUIDE_PDF, INSTALLATION_GUIDE_EN_PDF):
        if name != missing:
            (tmp_path / name).write_bytes(b"fixture")
    with pytest.raises(FileNotFoundError, match=re.escape(missing)):
        create_distribution_zip(tmp_path, tmp_path)
    assert not list(tmp_path.glob("*.zip*"))
