"""Read-only smoke test against the real vanilla Anomaly script corpus.

Skipped unless you pass --corpus. The game install is never written to: every
file is read, analyzed in-process and left alone. Nothing here calls --fix.
"""

import time

import pytest

from ast_analyzer import ASTAnalyzer
from conftest import VANILLA_CORPUS
from models import detect_file_encoding

pytestmark = pytest.mark.corpus


def _corpus_files():
    if not VANILLA_CORPUS.is_dir():
        pytest.skip(f"vanilla corpus not present at {VANILLA_CORPUS}")
    files = sorted(VANILLA_CORPUS.glob("*.script")) + sorted(VANILLA_CORPUS.glob("*.lua"))
    if not files:
        pytest.skip(f"no scripts under {VANILLA_CORPUS}")
    return files


def test_the_corpus_is_where_we_expect_it():
    files = _corpus_files()
    assert len(files) >= 60, f"only found {len(files)} scripts, corpus looks wrong"


def test_every_vanilla_script_analyzes_without_crashing_or_failing_to_parse():
    """Two failure modes, reported separately so the message is actionable.

    A crash is an ALAO bug outright. A parse failure is softer - analyze_file()
    swallows it and returns [] - but on vanilla Anomaly scripts, which the game
    itself loads, an empty result means ALAO simply cannot see the file.
    """
    files = _corpus_files()

    crashes = []
    parse_failures = []
    total_findings = 0
    started = time.time()

    for path in files:
        analyzer = ASTAnalyzer()
        try:
            findings = analyzer.analyze_file(path)
        except Exception as exc:  # noqa: BLE001 - we want every failure mode
            crashes.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue

        total_findings += len(findings)

        # analyze_file() returns [] both for "clean file" and "could not parse".
        # Distinguish them: a file it parsed has its source loaded.
        if not analyzer.source and path.stat().st_size > 0:
            parse_failures.append(f"{path.name}: source never loaded (read or parse failed)")
        elif analyzer._ast_tree is None:
            parse_failures.append(f"{path.name}: parsed to no AST")

    elapsed = time.time() - started
    print(
        f"\nanalyzed {len(files)} vanilla scripts in {elapsed:.1f}s, "
        f"{total_findings} findings"
    )

    assert not crashes, "ALAO crashed on:\n  " + "\n  ".join(crashes)
    assert not parse_failures, "ALAO could not parse:\n  " + "\n  ".join(parse_failures)


def test_encoding_detection_never_throws_on_the_corpus():
    problems = []
    for path in _corpus_files():
        try:
            encoding = detect_file_encoding(path)
            path.read_text(encoding=encoding)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{path.name}: {type(exc).__name__}: {exc}")
    assert not problems, "encoding detection failed on:\n  " + "\n  ".join(problems)


def test_the_corpus_is_not_modified():
    """Belt and braces: analysis must not touch the game install."""
    files = _corpus_files()
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in files}

    for path in files:
        ASTAnalyzer().analyze_file(path)

    after = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in files}
    changed = [p.name for p in files if before[p] != after[p]]
    assert not changed, f"analysis modified files in the game install: {changed}"

    assert not list(VANILLA_CORPUS.glob("*.alao-bak")), (
        "a .alao-bak was created inside the read-only game install"
    )
