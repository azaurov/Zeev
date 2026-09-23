"""stub_oww_verifier keeps openwakeword from importing sklearn/scipy.

openwakeword/__init__.py imports custom_verifier_model unconditionally, and
that module drags in sklearn + scipy: 100+ MB RSS and ~12.7s of startup on
the Pi Zero 2W (463 MB RAM), for a training feature Zeev never uses.

The real package isn't installed in dev, so a fake one on tmp_path mirrors
its import shape: __init__ -> model + custom_verifier_model, where the
verifier module records that it ran (standing in for the sklearn import).
"""
import sys
from pathlib import Path

import pytest

_PKG = {
    "__init__.py": (
        "from openwakeword.model import Model\n"
        "from openwakeword.custom_verifier_model import train_custom_verifier\n"
    ),
    "model.py": (
        "class Model:\n"
        "    def __init__(self, wakeword_model_paths=()):\n"
        "        self.paths = list(wakeword_model_paths)\n"
    ),
    "custom_verifier_model.py": (
        "import sys\n"
        "sys.modules['_fake_sklearn_loaded'] = True\n"
        "def train_custom_verifier(*a, **k):\n"
        "    return 'real'\n"
    ),
}


@pytest.fixture
def fake_oww(tmp_path, monkeypatch):
    pkg = tmp_path / "openwakeword"
    pkg.mkdir()
    for name, src in _PKG.items():
        (pkg / name).write_text(src)
    monkeypatch.syspath_prepend(str(tmp_path))
    for mod in [m for m in sys.modules
                if m == "openwakeword" or m.startswith("openwakeword.")
                or m == "_fake_sklearn_loaded"]:
        monkeypatch.delitem(sys.modules, mod)
    yield
    for mod in [m for m in sys.modules
                if m == "openwakeword" or m.startswith("openwakeword.")
                or m == "_fake_sklearn_loaded"]:
        sys.modules.pop(mod, None)


def test_stub_prevents_verifier_import_and_model_still_loads(zeev, fake_oww):
    zeev.stub_oww_verifier()
    from openwakeword.model import Model
    assert "_fake_sklearn_loaded" not in sys.modules
    # The thing the wake listener actually needs still works.
    assert Model(wakeword_model_paths=["a.onnx"]).paths == ["a.onnx"]
    with pytest.raises(RuntimeError):
        sys.modules["openwakeword"].train_custom_verifier()


def test_without_stub_verifier_is_imported(zeev, fake_oww):
    """Control: proves the fake package reproduces the upstream behaviour,
    so the test above is testing the stub and not the fixture."""
    from openwakeword.model import Model  # noqa: F401
    assert "_fake_sklearn_loaded" in sys.modules


def test_stub_never_replaces_a_loaded_real_module(zeev, fake_oww):
    import openwakeword  # real (fake-package) verifier loads
    zeev.stub_oww_verifier()
    assert openwakeword.train_custom_verifier() == "real"
    assert sys.modules[zeev._OWW_VERIFIER_MOD].train_custom_verifier() == "real"


def test_wake_listener_stubs_before_importing_openwakeword():
    """Structural: the call must precede the import inside the listener, or
    the stub is a no-op and the 100 MB comes back silently."""
    src = (Path(__file__).parent.parent / "zeev" / "zeev.py").read_text()
    imp = src.index("from openwakeword.model import Model as _OwwModel")
    call = src.rfind("stub_oww_verifier()", 0, imp)
    assert call != -1 and imp - call < 200
