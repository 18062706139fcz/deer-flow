from pathlib import Path

from deerflow.skills.review import LocalDirectoryReader, analyze_skill_package, stable_json_dumps
from deerflow.skills.review.cli import main as review_cli_main
from deerflow.skills.review.renderer import build_static_report, render_report_markdown


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _valid_skill(name: str = "demo-skill", description: str = "Demo skill. Invoke when testing review.") -> str:
    return f"---\nname: {name}\ndescription: {description}\nallowed-tools: []\n---\n\n# Demo\n\nFollow the steps and stop.\n"


def test_review_core_accepts_minimal_valid_skill(tmp_path):
    _write(tmp_path / "SKILL.md", _valid_skill())

    snapshot = LocalDirectoryReader(tmp_path).read()
    facts = analyze_skill_package(snapshot)

    assert facts["schema_version"] == "deerflow.skill-review.facts.v1"
    assert facts["subject"]["declared_name"] == "demo-skill"
    assert facts["summary"]["blockers"] == 0
    assert facts["subject"]["package_digest"].startswith("sha256:")


def test_review_core_reports_missing_description_blocker(tmp_path):
    _write(tmp_path / "SKILL.md", "---\nname: demo-skill\n---\n\n# Demo\n")

    facts = analyze_skill_package(LocalDirectoryReader(tmp_path).read())

    assert facts["summary"]["blockers"] >= 1
    assert any(f["rule_id"] == "structure.missing-description" for f in facts["findings"])


def test_resource_graph_reports_unreferenced_resource(tmp_path):
    _write(tmp_path / "SKILL.md", _valid_skill())
    _write(tmp_path / "references" / "unused.md", "# Unused\n")

    facts = analyze_skill_package(LocalDirectoryReader(tmp_path).read())

    assert "references/unused.md" in facts["resources"]["orphans"]
    assert any(f["rule_id"] == "resource.unreferenced" and f["path"] == "references/unused.md" for f in facts["findings"])


def test_resource_graph_tracks_referenced_resource(tmp_path):
    _write(tmp_path / "SKILL.md", _valid_skill() + "\nRead [guide](references/guide.md).\n")
    _write(tmp_path / "references" / "guide.md", "# Guide\n")

    facts = analyze_skill_package(LocalDirectoryReader(tmp_path).read())

    assert {"source": "SKILL.md", "target": "references/guide.md"} in facts["resources"]["edges"]
    assert "references/guide.md" not in facts["resources"]["orphans"]


def test_package_digest_is_path_independent(tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    _write(one / "SKILL.md", _valid_skill())
    _write(two / "SKILL.md", _valid_skill())

    facts_one = analyze_skill_package(LocalDirectoryReader(one).read())
    facts_two = analyze_skill_package(LocalDirectoryReader(two).read())

    assert facts_one["subject"]["package_digest"] == facts_two["subject"]["package_digest"]
    assert stable_json_dumps(facts_one).replace("one", "x") != ""


def test_skillscan_findings_are_adapted(tmp_path):
    _write(
        tmp_path / "SKILL.md",
        _valid_skill() + "\nNever include a private key:\n-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
    )

    facts = analyze_skill_package(LocalDirectoryReader(tmp_path).read())

    finding = next(f for f in facts["findings"] if f["source"] == "skillscan" and f["rule_id"] == "secret-private-key")
    assert finding["severity"] == "blocker"
    assert finding["skillscan_severity"] == "CRITICAL"


def test_static_report_renders_chinese_labels(tmp_path):
    _write(tmp_path / "SKILL.md", _valid_skill())
    facts = analyze_skill_package(LocalDirectoryReader(tmp_path).read())

    report = build_static_report(facts, completed_at="2026-07-10T00:00:00Z")
    markdown = render_report_markdown(report, facts, locale="zh")

    assert report["schema_version"] == "deerflow.skill-review.report.v1"
    assert "## 摘要" in markdown
    assert "publish_candidate" in markdown


def test_cli_fail_on_error(tmp_path, capsys):
    _write(tmp_path / "SKILL.md", "---\nname: demo-skill\n---\n\n# Demo\n")

    exit_code = review_cli_main([str(tmp_path), "--format", "text", "--fail-on", "blocker"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "structure.missing-description" in output
