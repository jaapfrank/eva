"""Package bytes/provenance are data; loading must never execute a skill."""
from dataclasses import replace
from copy import deepcopy
import pytest
from eva.application.skill_packages import AgentSkillPackage, SkillResource, SkillPackageRejected, package_path


def package():
    return AgentSkillPackage("https://github.com/example/skills", "a" * 40, "skills/example", "MIT", "LICENSE", (
        SkillResource("SKILL.md", b"---\nname: useful-skill\ndescription: Reuse a useful procedure.\nallowed-tools: [host_admin]\n---\nUse scripts/helper.py and references/note.md.\n"),
        SkillResource("scripts/helper.py", b"raise RuntimeError('must never run while importing')\n"),
        SkillResource("references/note.md", b"Source data, not additional permission.\n"),
        SkillResource("LICENSE", b"MIT license notice\n")))


def test_all_bytes_and_relative_paths_roundtrip_without_executing_support_code():
    current = package();restored = AgentSkillPackage.from_wire(current.to_wire())
    assert restored.sha256 == current.sha256
    assert restored.files == tuple(sorted(current.files, key=lambda f:f.path))
    assert restored.resource("scripts/helper.py") == current.resource("scripts/helper.py")
    assert restored.metadata["allowed-tools"] == ["host_admin"]  # Still inert package data.
    assert set(restored.manifest()) == {"contract", "origin", "files"}


@pytest.mark.parametrize("path", [".", "..", "../secret", "/absolute", "a/../b", "a//b", "a/./b", "C:/temp", "a\\b", "a\x00b"])
def test_escaping_or_ambiguous_resource_paths_are_not_normalized(path):
    with pytest.raises(SkillPackageRejected):package_path(path)


@pytest.mark.parametrize("field", ["file_bytes", "file_digest", "package_digest", "source_revision"])
def test_modified_package_bytes_and_provenance_do_not_keep_the_original_identity(field):
    wire = deepcopy(package().to_wire())
    if field == "file_bytes":wire["files"][0]["content_base64"] = "YWJj"
    elif field == "file_digest":wire["files"][0]["sha256"] = "f" * 64
    elif field == "package_digest":wire["package_sha256"] = "f" * 64
    else:wire["origin"]["revision"] = "b" * 40
    with pytest.raises(SkillPackageRejected):AgentSkillPackage.from_wire(wire)


@pytest.mark.parametrize("change", ["missing_license", "missing_skill", "case_duplicate", "floating_revision"])
def test_incomplete_or_ambiguous_package_is_rejected(change):
    current = package()
    with pytest.raises(SkillPackageRejected):
        if change == "missing_license":replace(current, files=tuple(f for f in current.files if f.path != "LICENSE"))
        elif change == "missing_skill":replace(current, files=tuple(f for f in current.files if f.path != "SKILL.md"))
        elif change == "case_duplicate":replace(current, files=current.files + (SkillResource("license", b"conflict"),))
        else:replace(current, revision="main")


@pytest.mark.parametrize("frontmatter", [
    "name: first\nname: second\ndescription: Duplicate name",
    "name: first\ndescription: &text hello\nmetadata: *text",
    "name: first\ndescription: !!python/object/apply:builtins.print [must-not-run]",
])
def test_ambiguous_or_executable_yaml_is_not_used_as_metadata(frontmatter):
    current = package();raw = ("---\n" + frontmatter + "\n---\nBody").encode()
    with pytest.raises(SkillPackageRejected):
        replace(current, files=tuple(SkillResource(f.path,raw) if f.path=="SKILL.md" else f for f in current.files))


@pytest.mark.parametrize("path", ["LICENSE.", "LICENSE ", "CON", "aux.txt", "a/NUL.log", "a/foo?bar"])
def test_host_portability_aliases_are_rejected_before_materialization(path):
    with pytest.raises(SkillPackageRejected):package_path(path)


def test_file_cannot_alias_a_directory_needed_by_another_resource():
    current=package()
    with pytest.raises(SkillPackageRejected):
        replace(current,files=current.files+(SkillResource("scripts",b"not a directory"),
                                            SkillResource("scripts-extra",b"sorting neighbor"),))


@pytest.mark.parametrize("subdirectory", [None, 0, []])
def test_falsey_nontext_source_directory_is_rejected(subdirectory):
    with pytest.raises(SkillPackageRejected):replace(package(),subdirectory=subdirectory)


def test_deep_metadata_is_a_typed_rejection_not_a_recursion_crash():
    current=package()
    raw=("---\nname: useful-skill\ndescription: ok\nmetadata: " + "["*1500+"0"+"]"*1500+"\n---\nBody").encode()
    with pytest.raises(SkillPackageRejected):
        replace(current,files=tuple(SkillResource(f.path,raw) if f.path=="SKILL.md" else f for f in current.files))
