from app import desktop


def test_free_port_and_import():
    assert 1024 < desktop.free_port() < 65536
