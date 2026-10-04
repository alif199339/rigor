"""Offline checks on the Claude Code plugin packaging (.claude-plugin/).

`claude plugin validate` is the authoritative validator, but it needs the Claude Code CLI,
which CI does not install. These tests pin the parts that break silently or that the
validator tolerates: a drifted version, a skill folder the plugin would not expose, a
hardcoded install path reintroduced into a SKILL.md, and the two documented footguns --
a `skills` key on the marketplace entry (which suppresses the default `skills/` scan) and
a top-level `bin/` directory (which stops claude.ai and Cowork installing the plugin)."""
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_DIR = os.path.join(ROOT, ".claude-plugin")
SKILLS_DIR = os.path.join(ROOT, "skills")

# Names Anthropic reserves for its own plugins (manifest reference, `name`).
RESERVED_PREFIXES = ("claude-", "anthropic-", "anthropics-", "cc-plugin-")
RESERVED_EXACT = ("claude", "anthropic", "anthropics", "claude-code", "claude-mods")


def _load(name):
    with open(os.path.join(PLUGIN_DIR, name), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def plugin():
    return _load("plugin.json")


@pytest.fixture(scope="module")
def marketplace():
    return _load("marketplace.json")


def skill_dirs():
    """Every real skill folder: a directory under skills/ holding a SKILL.md."""
    return sorted(
        d for d in os.listdir(SKILLS_DIR)
        if os.path.isfile(os.path.join(SKILLS_DIR, d, "SKILL.md"))
    )


# --------------------------------------------------------------------- manifest

def test_plugin_manifest_has_the_metadata_the_validator_warns_about(plugin):
    for key in ("name", "version", "description", "author", "license"):
        assert plugin.get(key), f"plugin.json is missing {key}"
    assert plugin["author"].get("name"), "author.name is required"


def test_plugin_name_is_kebab_case_and_not_reserved(plugin):
    name = plugin["name"]
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name), f"{name!r} is not kebab-case"
    low = name.lower()
    assert not low.startswith(RESERVED_PREFIXES), f"{name!r} uses a reserved prefix"
    assert low not in RESERVED_EXACT, f"{name!r} is a reserved name"
    # A whole-word claude/anthropic anywhere draws a validator warning.
    assert not re.search(r"\b(claude|anthropics?)\b", low.replace("-", " ")), \
        f"{name!r} reads as one of Anthropic's own plugins"


def test_plugin_declares_no_component_paths_that_do_not_exist(plugin):
    """Every component path must start with ./ and resolve inside the plugin root."""
    for key in ("skills", "commands", "agents", "hooks", "outputStyles", "workflows"):
        val = plugin.get(key)
        if val is None:
            continue
        paths = [val] if isinstance(val, str) else val
        for p in paths:
            if not isinstance(p, str):
                continue
            assert p in (".", "./") or p.startswith("./"), f"{key}: {p!r} must start with ./"
            assert ".." not in p, f"{key}: {p!r} escapes the plugin root"
            assert os.path.exists(os.path.join(ROOT, p)), f"{key}: {p!r} does not exist"


def test_no_top_level_bin_directory():
    """A bin/ directory stops claude.ai and Cowork from installing the plugin."""
    assert not os.path.isdir(os.path.join(ROOT, "bin")), \
        "a top-level bin/ blocks installation on claude.ai and Cowork"


# ------------------------------------------------------------------ marketplace

def test_marketplace_has_required_fields(marketplace):
    assert marketplace.get("name")
    assert marketplace.get("owner", {}).get("name")
    assert marketplace.get("plugins"), "marketplace lists no plugins"
    assert marketplace.get("description"), "a missing description is a validator warning"


def test_marketplace_entry_points_at_this_repo_and_adds_no_components(marketplace, plugin):
    entries = marketplace["plugins"]
    assert len(entries) == 1, "expected exactly one plugin entry"
    e = entries[0]
    assert e["name"] == plugin["name"], "entry name must match plugin.json's name"
    assert e["source"] == ".", "the plugin is this repository root"
    # An entry `skills` key on a root-sourced entry means ONLY those subdirectories
    # load, silently shadowing the default skills/ scan.
    assert "skills" not in e, "an entry-level skills key suppresses the default skills/ scan"
    # plugin.json's version wins; declaring it twice is a validator warning.
    assert "version" not in e, "declare version in plugin.json only"


# ------------------------------------------------------------------------ skills

def test_every_skill_folder_is_well_formed():
    dirs = skill_dirs()
    assert len(dirs) == 16, f"expected 16 skills, found {len(dirs)}: {dirs}"
    for d in dirs:
        text = open(os.path.join(SKILLS_DIR, d, "SKILL.md"), encoding="utf-8").read()
        m = re.match(r"---\nname: (.+?)\n", text)
        assert m, f"{d}/SKILL.md has no YAML frontmatter name"
        assert m.group(1).strip() == d, \
            f"{d}/SKILL.md declares name {m.group(1)!r}, which must match its folder"


def test_every_skill_frontmatter_is_valid_yaml():
    """Frontmatter that fails to parse loads the skill with EMPTY metadata -- silently.

    A plain YAML scalar may not contain ': ', so an unquoted description ending
    '... (STGCN: CLAUDE.md §4/§6.3/§6.4).' parses as nothing and the skill's name and
    description never reach the model, which stops it being invoked on description match.
    Found live in a project install on 2026-10-04; quoting the scalar is the fix."""
    yaml = pytest.importorskip("yaml")
    for d in skill_dirs():
        text = open(os.path.join(SKILLS_DIR, d, "SKILL.md"), encoding="utf-8").read()
        m = re.match(r"---\n(.*?)\n---\n", text, flags=re.S)
        assert m, f"{d}/SKILL.md has no frontmatter block"
        try:
            meta = yaml.safe_load(m.group(1))
        except yaml.YAMLError as e:
            raise AssertionError(f"{d}/SKILL.md frontmatter is not valid YAML: {e}") from None
        assert isinstance(meta, dict), f"{d}/SKILL.md frontmatter is not a mapping"
        assert meta.get("name") == d, f"{d}/SKILL.md declares name {meta.get('name')!r}"
        assert meta.get("description"), f"{d}/SKILL.md has no description"


def test_plugin_version_matches_the_manifest_changelog(plugin):
    """plugin.json and RESEARCH_AGENT.md must not drift apart."""
    text = open(os.path.join(SKILLS_DIR, "RESEARCH_AGENT.md"), encoding="utf-8").read()
    m = re.search(r"\*\*VERSION:\s*([0-9.]+)\*\*", text)
    assert m, "RESEARCH_AGENT.md has no VERSION line"
    assert plugin["version"] == m.group(1), (
        f"plugin.json says {plugin['version']}, RESEARCH_AGENT.md says {m.group(1)}")


def test_skill_docs_carry_the_install_path_note():
    for d in skill_dirs():
        text = open(os.path.join(SKILLS_DIR, d, "SKILL.md"), encoding="utf-8").read()
        assert "<!-- rigor:paths -->" in text, f"{d}/SKILL.md lost its install-path note"


# An actually-runnable script path, as opposed to a prose mention like `.claude/skills/...`.
RUNNABLE_PATH = re.compile(r"\.claude[/\\]skills[/\\][A-Za-z0-9_-]+[/\\][A-Za-z0-9_]+\.py")


def test_no_skill_hardcodes_the_copy_paste_install_path():
    """Script paths are written $RIGOR/skills/... so they resolve in a plugin install too.

    submit-gate is the one exception: its gate.yaml example carries literal argv that
    submit_gate.py executes, where no variable is expanded. Those paths are documented
    as literal, so they must stay inside the fenced yaml block."""
    for d in skill_dirs():
        path = os.path.join(SKILLS_DIR, d, "SKILL.md")
        text = open(path, encoding="utf-8").read()
        if d == "submit-gate":
            text = re.sub(r"```yaml\n.*?```", "", text, flags=re.S)
            msg = "submit-gate: literal install paths belong inside the gate.yaml block"
        else:
            msg = f"{d}/SKILL.md hardcodes an install path; use $RIGOR/skills/..."
        assert not RUNNABLE_PATH.search(text), msg
