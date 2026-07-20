"""Pure port-parsing tests for the Vite supervisor (Step 3.4)."""
from sage.preview.supervisor import parse_vite_url


def test_parses_local_line():
    assert parse_vite_url("  ->  Local:   http://localhost:5173/") == "http://localhost:5173"
    assert parse_vite_url("  Local:   http://127.0.0.1:5199/") == "http://127.0.0.1:5199"


def test_ignores_network_and_noise():
    assert parse_vite_url("  Network: http://10.0.0.2:5173/") is None
    assert parse_vite_url("VITE v8 ready in 76 ms") is None
