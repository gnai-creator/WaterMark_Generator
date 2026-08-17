import pytest
from watermark_generator.storage import Store
from watermark_generator.core import initialize


@pytest.fixture
def repo(tmp_path):
    store = Store(tmp_path)
    initialize(store, "correct horse battery staple")
    return store


@pytest.fixture
def passphrase():
    return "correct horse battery staple"
