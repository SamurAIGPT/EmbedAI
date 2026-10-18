"""Knowing that the server is stopping, before its shutdown starts.

Uvicorn runs the app's shutdown only after every open connection has closed,
and by default waits for that without a time limit. An event stream (the web
page keeps one open per conversation) never closes by itself, so a SIGTERM
used to leave the backend waiting forever, with its turns still running.

`install_signal_hooks` (called at startup) chains onto the server's own
SIGTERM/SIGINT handlers and marks the process as stopping; the event streams
see that within a second and end, so the connections close and the shutdown
(interrupting turns, killing engine processes) runs. The desktop launcher's
lifeline marks it the same way (app/desktop/entry.py).
"""

import signal
import threading

_stopping = threading.Event()
_HOOK_MARK = "_open_dots_stopping_hook"


def stopping() -> bool:
    return _stopping.is_set()


def begin() -> None:
    """The server is stopping: long-lived responses should end now."""
    _stopping.set()


def reset() -> None:
    """Startup of a (re)started app in this process."""
    _stopping.clear()


def install_signal_hooks() -> None:
    """Chain onto the current SIGTERM/SIGINT handlers. Only possible from the
    main thread (the server's signal handlers live there too); elsewhere it
    does nothing, and the server's graceful timeout is the backstop."""
    if threading.current_thread() is not threading.main_thread():
        return
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous = signal.getsignal(sig)
        if getattr(previous, _HOOK_MARK, False):
            continue

        def hook(signum, frame, previous=previous):
            begin()
            if callable(previous):
                previous(signum, frame)
            elif previous == signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                signal.raise_signal(signum)

        setattr(hook, _HOOK_MARK, True)
        try:
            signal.signal(sig, hook)
        except (ValueError, OSError):
            return
