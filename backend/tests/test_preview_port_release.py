"""A retiring preview listener must settle before its replacement process starts."""
import errno
import socket
import threading
import time

import pytest

from sage.preview import supervisor as preview

from .test_preview_reload_status import _generation_fixture


@pytest.mark.parametrize('supervisor', [preview.ViteSupervisor, preview.UvicornSupervisor])
def test_stop_during_port_wait_cancels_launch_without_blocking(tmp_path, monkeypatch, supervisor):
    sup = _generation_fixture(tmp_path, supervisor)
    entered, release = threading.Event(), threading.Event()
    launched = []

    def busy(port):
        entered.set()
        assert release.wait(2)
        raise OSError(errno.EADDRINUSE, 'old listener still closing')

    monkeypatch.setattr(sup, '_clear_stale_port', lambda port: None)
    monkeypatch.setattr(preview, '_probe_port', busy)
    monkeypatch.setattr(preview.subprocess, 'Popen', lambda *args, **kwargs: launched.append(args))
    try:
        assert sup.retry_start(explicit=True)
        assert entered.wait(1)
        before = time.monotonic()
        assert sup.status()['state'] == 'starting'
        sup.stop()
        assert time.monotonic() - before < 0.5
        release.set()
        sup._retry_thread.join(2)
        assert not sup._retry_thread.is_alive()
        assert launched == []
        assert sup.status()['state'] == 'failed'
        assert 'Preview stopped' in sup.status()['error']
    finally:
        release.set()
        sup.stop()
        sup._retry_thread.join(2)


def test_busy_port_has_a_bounded_explicit_startup_failure(tmp_path, monkeypatch):
    sup = _generation_fixture(tmp_path, preview.UvicornSupervisor)
    launched = []

    def busy(port):
        raise OSError(errno.EADDRINUSE, 'old listener still closing')

    monkeypatch.setattr(sup, '_clear_stale_port', lambda port: None)
    monkeypatch.setattr(preview, '_probe_port', busy)
    monkeypatch.setattr(preview, '_PORT_RELEASE_TIMEOUT_S', 0.05)
    monkeypatch.setattr(preview.subprocess, 'Popen', lambda *args, **kwargs: launched.append(args))
    before = time.monotonic()
    with pytest.raises(RuntimeError, match=r'Preview port \d+ is still in use after 0.05s'):
        sup.start(ready_timeout_s=1)
    assert time.monotonic() - before < 0.5
    assert launched == []
    assert sup.status()['state'] == 'failed'
    assert 'still in use' in sup.status()['error']


@pytest.mark.parametrize('error', [errno.EACCES, errno.EINVAL, errno.EADDRNOTAVAIL])
def test_other_port_errors_are_not_retried_as_busy(tmp_path, monkeypatch, error):
    sup = preview.ViteSupervisor(tmp_path)
    failure = OSError(error, 'distinct socket configuration failure')
    calls = []

    def fail(port):
        calls.append(port)
        raise failure

    monkeypatch.setattr(preview, '_probe_port', fail)
    with pytest.raises(OSError) as caught:
        sup._wait_for_port_release(7, 0)
    assert caught.value is failure
    assert calls == [7]


@pytest.mark.parametrize('error', [errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT,
                                  errno.EADDRNOTAVAIL, errno.ENODEV, errno.EACCES])
def test_ipv4_only_host_can_launch_but_ipv6_permission_errors_remain_visible(monkeypatch, error):
    real_socket = socket.socket
    families = []

    def ipv4_only(family, kind):
        families.append(family)
        if family == socket.AF_INET6:
            raise OSError(error, 'IPv6 unavailable')
        return real_socket(family, kind)

    monkeypatch.setattr(preview.socket, 'socket', ipv4_only)
    if error == errno.EACCES:
        with pytest.raises(OSError) as caught:
            preview._probe_port(0)
        assert caught.value.errno == errno.EACCES
    else:
        preview._probe_port(0)
    assert families == [socket.AF_INET, socket.AF_INET, socket.AF_INET6] + (
        [] if error == errno.EACCES else [socket.AF_INET6]
    )


@pytest.mark.parametrize('family,host', [(socket.AF_INET, '127.0.0.1'), (socket.AF_INET, '0.0.0.0'),
                                        (socket.AF_INET6, '::1'), (socket.AF_INET6, '::')])
def test_probe_detects_each_preview_listener_address(family, host):
    try:
        with socket.socket(family, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host, 0))
            listener.listen(1)
            with pytest.raises(OSError) as caught:
                preview._probe_port(listener.getsockname()[1])
            assert caught.value.errno == errno.EADDRINUSE
    except OSError as exc:
        if family == socket.AF_INET6 and exc.errno in (
            errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL, errno.ENODEV,
        ):
            pytest.skip('Host has no IPv6 listener support; simulated IPv4-only cases still run')
        raise
