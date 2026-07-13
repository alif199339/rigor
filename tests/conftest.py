"""Load the skill scripts (which live in hyphenated folders) as importable modules.
Every test is offline: any HTTP entry point used by a test is monkeypatched."""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(skill, script):
    path = os.path.join(ROOT, "skills", skill, script)
    name = script[:-3]
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # lets topic_watch's `import lit_search` resolve to this
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def lit():
    return load("lit-review", "lit_search.py")


@pytest.fixture(scope="session")
def bib():
    return load("bib-audit", "bib_audit.py")


@pytest.fixture(scope="session")
def claims():
    return load("claims-audit", "claims_audit.py")


@pytest.fixture(scope="session")
def stat():
    return load("stat-check", "stat_check.py")


@pytest.fixture(scope="session")
def watch(lit):
    # lit must be loaded first so topic_watch reuses the same lit_search module object
    return load("topic-watch", "topic_watch.py")


@pytest.fixture(scope="session")
def colab():
    return load("colab-run", "colab_run.py")


@pytest.fixture(scope="session")
def labnb():
    return load("lab-notebook", "notebook.py")


@pytest.fixture(scope="session")
def verify():
    return load("verify-run", "verify_run.py")


@pytest.fixture(scope="session")
def runner():
    # templates/runner.py lives outside skills/, so load it directly. papermill is
    # imported lazily inside prepare_run(), so this import needs only pyyaml.
    path = os.path.join(ROOT, "templates", "runner.py")
    name = "runner_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
