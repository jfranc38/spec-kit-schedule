"""Tests for solver._paths — encapsulated path constants and migration helper."""

from __future__ import annotations

__all__: list[str] = []

from pathlib import Path

from solver._paths import (
    EXTENSION_ID,
    encapsulated_venv_python,
    extension_code_dir,
    extension_state_dir,
    legacy_config_path,
    migrate_legacy_config,
    project_root,
    schedule_config_path,
)

# ---------------------------------------------------------------------------
# project_root
# ---------------------------------------------------------------------------


class TestProjectRoot:
    def test_finds_specify_marker_at_start(self, tmp_path: Path) -> None:
        (tmp_path / ".specify").mkdir()
        assert project_root(tmp_path) == tmp_path.resolve()

    def test_walks_up_to_marker(self, tmp_path: Path) -> None:
        (tmp_path / ".specify").mkdir()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert project_root(nested) == tmp_path.resolve()

    def test_no_marker_returns_start(self, tmp_path: Path) -> None:
        # No .specify anywhere → returns start path resolved
        assert project_root(tmp_path) == tmp_path.resolve()


# ---------------------------------------------------------------------------
# Path constructors
# ---------------------------------------------------------------------------


class TestExtensionPaths:
    def test_extension_code_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".specify").mkdir()
        assert extension_code_dir(tmp_path) == tmp_path / ".specify" / "extensions" / EXTENSION_ID

    def test_extension_state_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".specify").mkdir()
        assert extension_state_dir(tmp_path) == tmp_path / ".specify" / EXTENSION_ID

    def test_schedule_config_path(self, tmp_path: Path) -> None:
        (tmp_path / ".specify").mkdir()
        expected = tmp_path / ".specify" / EXTENSION_ID / "schedule-config.yml"
        assert schedule_config_path(tmp_path) == expected

    def test_encapsulated_venv_python(self, tmp_path: Path) -> None:
        (tmp_path / ".specify").mkdir()
        expected = (
            tmp_path / ".specify" / "extensions" / EXTENSION_ID / ".venv" / "bin" / "python"
        )
        assert encapsulated_venv_python(tmp_path) == expected

    def test_extension_id_is_schedule(self) -> None:
        assert EXTENSION_ID == "schedule"


# ---------------------------------------------------------------------------
# migrate_legacy_config
# ---------------------------------------------------------------------------


class TestMigrateLegacyConfig:
    def test_no_legacy_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / ".specify").mkdir()
        assert migrate_legacy_config(tmp_path) is None

    def test_legacy_moved_to_encapsulated(self, tmp_path: Path) -> None:
        (tmp_path / ".specify").mkdir()
        legacy = legacy_config_path(tmp_path)
        legacy.write_text("agents: []\n", encoding="utf-8")

        new_path = migrate_legacy_config(tmp_path)

        assert new_path == schedule_config_path(tmp_path)
        assert new_path is not None
        assert new_path.is_file()
        assert new_path.read_text(encoding="utf-8") == "agents: []\n"
        # Legacy is gone
        assert not legacy.exists()

    def test_state_dir_created_if_missing(self, tmp_path: Path) -> None:
        (tmp_path / ".specify").mkdir()
        legacy = legacy_config_path(tmp_path)
        legacy.write_text("# config\n", encoding="utf-8")
        # state dir does not exist yet
        assert not extension_state_dir(tmp_path).exists()

        new_path = migrate_legacy_config(tmp_path)
        assert new_path is not None and new_path.is_file()
        assert extension_state_dir(tmp_path).is_dir()

    def test_does_not_overwrite_existing_new(self, tmp_path: Path) -> None:
        """When BOTH paths exist, leave them alone and return None.

        The user may have edited the new file; silently overwriting it
        would be a regression. Conservative behaviour wins.
        """
        (tmp_path / ".specify").mkdir()
        legacy = legacy_config_path(tmp_path)
        legacy.write_text("legacy content\n", encoding="utf-8")
        new_path = schedule_config_path(tmp_path)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text("new content\n", encoding="utf-8")

        result = migrate_legacy_config(tmp_path)
        assert result is None
        # Both files preserved
        assert legacy.read_text(encoding="utf-8") == "legacy content\n"
        assert new_path.read_text(encoding="utf-8") == "new content\n"

    def test_idempotent_after_migration(self, tmp_path: Path) -> None:
        (tmp_path / ".specify").mkdir()
        legacy = legacy_config_path(tmp_path)
        legacy.write_text("agents: []\n", encoding="utf-8")
        first = migrate_legacy_config(tmp_path)
        assert first is not None
        # Second call is no-op
        second = migrate_legacy_config(tmp_path)
        assert second is None
