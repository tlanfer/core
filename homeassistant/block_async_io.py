"""Block blocking calls being done in asyncio."""

import builtins
from contextlib import suppress
import glob
from http.client import HTTPConnection
import importlib
import os
import sys
import threading
import time
from typing import Any

from .helpers.frame import get_current_frame
from .util.loop import protect_loop

_IN_TESTS = "unittest" in sys.modules

ALLOWED_FILE_PREFIXES = ("/proc",)


def _check_import_call_allowed(mapped_args: dict[str, Any]) -> bool:
    # If the module is already imported, we can ignore it.
    return bool((args := mapped_args.get("args")) and args[0] in sys.modules)


def _check_file_allowed(mapped_args: dict[str, Any]) -> bool:
    # If the file is in /proc we can ignore it.
    args = mapped_args["args"]
    path = args[0] if type(args[0]) is str else str(args[0])  # noqa: E721
    return path.startswith(ALLOWED_FILE_PREFIXES)


def _check_sleep_call_allowed(mapped_args: dict[str, Any]) -> bool:
    #
    # Avoid extracting the stack unless we need to since it
    # will have to access the linecache which can do blocking
    # I/O and we are trying to avoid blocking calls.
    #
    # frame[0] is us
    # frame[1] is raise_for_blocking_call
    # frame[2] is protected_loop_func
    # frame[3] is the offender
    with suppress(ValueError):
        return get_current_frame(4).f_code.co_filename.endswith("pydevd.py")
    return False


def enable() -> None:
    """Enable the detection of blocking calls in the event loop."""
    loop_thread_id = threading.get_ident()
    # Prevent urllib3 and requests doing I/O in event loop
    HTTPConnection.putrequest = protect_loop(  # type: ignore[method-assign]
        HTTPConnection.putrequest, loop_thread_id=loop_thread_id
    )

    # Prevent sleeping in event loop.
    time.sleep = protect_loop(
        time.sleep,
        check_allowed=_check_sleep_call_allowed,
        loop_thread_id=loop_thread_id,
    )

    glob.glob = protect_loop(
        glob.glob, strict_core=False, strict=False, loop_thread_id=loop_thread_id
    )
    glob.iglob = protect_loop(
        glob.iglob, strict_core=False, strict=False, loop_thread_id=loop_thread_id
    )

    if not _IN_TESTS:
        # Prevent files being opened inside the event loop
        os.listdir = protect_loop(  # type: ignore[assignment]
            os.listdir, strict_core=False, strict=False, loop_thread_id=loop_thread_id
        )
        os.scandir = protect_loop(  # type: ignore[assignment]
            os.scandir, strict_core=False, strict=False, loop_thread_id=loop_thread_id
        )

        builtins.open = protect_loop(  # type: ignore[assignment]
            builtins.open,
            strict_core=False,
            strict=False,
            check_allowed=_check_file_allowed,
            loop_thread_id=loop_thread_id,
        )
        # unittest uses `importlib.import_module` to do mocking
        # so we cannot protect it if we are running tests
        importlib.import_module = protect_loop(
            importlib.import_module,
            strict_core=False,
            strict=False,
            check_allowed=_check_import_call_allowed,
            loop_thread_id=loop_thread_id,
        )
