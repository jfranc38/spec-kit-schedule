"""Tests for WarningCollector as a logging.Handler subclass."""

from __future__ import annotations

import logging

import pytest

from solver.warnings_collector import Warning_, WarningCollector


class TestWarningCollectorAsHandler:
    def test_emit_via_logger(self, capsys):
        collector = WarningCollector()
        logger = logging.getLogger("test.handler")
        logger.addHandler(collector)
        logger.setLevel(logging.WARNING)
        logger.warning("something went wrong")
        logger.removeHandler(collector)

        assert len(collector) == 1
        w = list(collector)[0]
        assert "something went wrong" in w.message

    def test_emit_stores_warning_dataclass(self):
        collector = WarningCollector()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="direct emit",
            args=(),
            exc_info=None,
        )
        record.code = "test_code"  # type: ignore[attr-defined]
        record.context = {"k": "v"}  # type: ignore[attr-defined]
        collector.handle(record)

        assert len(collector) == 1
        w = list(collector)[0]
        assert isinstance(w, Warning_)
        assert w.code == "test_code"
        assert "direct emit" in w.message
        assert w.context == {"k": "v"}

    def test_emit_defaults_code_to_levelname(self):
        collector = WarningCollector()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="no code attr",
            args=(),
            exc_info=None,
        )
        collector.handle(record)

        w = list(collector)[0]
        assert w.code == "WARNING"

    def test_emit_echoes_to_stderr(self, capsys):
        collector = WarningCollector()
        collector.add("my_code", "visible warning")
        captured = capsys.readouterr()
        assert "my_code" in captured.err
        assert "visible warning" in captured.err


class TestLegacyAddApi:
    def test_add_creates_warning(self, capsys):
        collector = WarningCollector()
        collector.add("test_code", "test message", extra="value")
        assert len(collector) == 1
        w = list(collector)[0]
        assert w.code == "test_code"
        assert w.message == "test message"
        assert w.context == {"extra": "value"}

    def test_add_echoes_to_stderr(self, capsys):
        collector = WarningCollector()
        collector.add("warn_code", "warn msg")
        captured = capsys.readouterr()
        assert "warn_code" in captured.err
        assert "warn msg" in captured.err

    def test_multiple_adds(self):
        collector = WarningCollector()
        collector.add("c1", "m1")
        collector.add("c2", "m2")
        assert len(collector) == 2


class TestAggregationHelpers:
    def test_extend_merges_warnings(self):
        c1 = WarningCollector()
        c1.add("code1", "msg1")
        c2 = WarningCollector()
        c2.add("code2", "msg2")
        c1.extend(c2)
        assert len(c1) == 2
        codes = {w.code for w in c1}
        assert codes == {"code1", "code2"}

    def test_as_list_returns_dicts(self):
        collector = WarningCollector()
        collector.add("c", "m", key="val")
        result = collector.as_list()
        assert isinstance(result, list)
        assert len(result) == 1
        d = result[0]
        assert d["code"] == "c"
        assert d["message"] == "m"
        assert d["context"] == {"key": "val"}

    def test_iter_yields_warning_objects(self):
        collector = WarningCollector()
        collector.add("code", "msg")
        items = list(collector)
        assert len(items) == 1
        assert isinstance(items[0], Warning_)

    def test_len_zero_on_empty(self):
        assert len(WarningCollector()) == 0


class TestHandlerLevel:
    def test_below_level_not_stored(self, capsys):
        collector = WarningCollector(level=logging.ERROR)
        logger = logging.getLogger("test.level")
        logger.addHandler(collector)
        logger.setLevel(logging.DEBUG)
        logger.warning("below threshold")
        logger.removeHandler(collector)
        assert len(collector) == 0

    def test_at_or_above_level_stored(self, capsys):
        collector = WarningCollector(level=logging.WARNING)
        logger = logging.getLogger("test.level2")
        logger.addHandler(collector)
        logger.setLevel(logging.DEBUG)
        logger.error("above threshold")
        logger.removeHandler(collector)
        assert len(collector) == 1


class TestTypeCompatibility:
    def test_is_logging_handler(self):
        assert isinstance(WarningCollector(), logging.Handler)

    def test_init_signature(self):
        c = WarningCollector(level=logging.ERROR)
        assert c.level == logging.ERROR

    def test_default_level_is_warning(self):
        c = WarningCollector()
        assert c.level == logging.WARNING

    @pytest.mark.parametrize("n", [0, 1, 5])
    def test_len_matches_add_calls(self, n):
        c = WarningCollector()
        for i in range(n):
            c.add(f"code{i}", f"msg{i}")
        assert len(c) == n
