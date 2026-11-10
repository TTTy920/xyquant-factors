"""macOS primitives for the shared XYQuant client."""

import contextlib
import ctypes
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import re
import time


class ClientError(Exception):
    def __init__(self, message, code="client_error"):
        super().__init__(message)
        self.code = code


def app_dir():
    return Path.home() / "Library" / "Application Support" / "XYQuant" / "FactorConnect"


@contextlib.contextmanager
def mutex(name, timeout=60, root=None):
    if not re.fullmatch(r"[a-z0-9-]{1,64}", name):
        raise ValueError("Unknown local lock")
    directory = (Path(root) if root else app_dir()) / "locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(directory / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ClientError("另一个连接操作正在进行，请稍后重试。", "busy") from None
                time.sleep(min(0.1, remaining))
        yield
    finally:
        os.close(descriptor)


def publish_download(partial, destination):
    """Publish the already verified file without replacing a concurrently created file."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renamex_np
    rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(os.fsencode(partial), os.fsencode(destination), 0x00000004) == 0:  # RENAME_EXCL
        return
    status = ctypes.get_errno()
    if status in (errno.ENOTSUP, errno.EINVAL):
        try:
            os.link(partial, destination, follow_symlinks=False)
            return
        except OSError as error:
            status = error.errno
    if status == errno.EEXIST:
        raise ClientError("目标文件已存在，未覆盖。", "file_conflict")
    if status == errno.ENOSPC:
        raise ClientError("本地磁盘空间不足，文件未保存。", "disk_full")
    raise ClientError("无法将已校验的文件保存到项目目录。", "invalid_path")


_POINTER = ctypes.c_void_p
_UINT = ctypes.c_uint32
_NOT_FOUND = -25300


class Credentials:
    """Keep the OAuth JSON in the user's Keychain, including token rotation and deletion."""

    service = b"org.xyquant.factors.oauth"

    def __init__(self, root):
        self.account = hashlib.sha256(os.fsencode(Path(root).resolve())).hexdigest().encode("ascii")
        self.security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.core.CFRelease.argtypes = [_POINTER]
        self.core.CFRelease.restype = None
        match = [_POINTER, _UINT, ctypes.c_char_p, _UINT, ctypes.c_char_p]
        signatures = {
            "SecKeychainFindGenericPassword": match + [ctypes.POINTER(_UINT), ctypes.POINTER(_POINTER), ctypes.POINTER(_POINTER)],
            "SecKeychainAddGenericPassword": match + [_UINT, _POINTER, ctypes.POINTER(_POINTER)],
            "SecKeychainItemModifyAttributesAndData": [_POINTER, _POINTER, _UINT, _POINTER],
            "SecKeychainItemDelete": [_POINTER],
            "SecKeychainItemFreeContent": [_POINTER, _POINTER],
        }
        for name, types in signatures.items():
            func = getattr(self.security, name)
            func.argtypes = types
            func.restype = ctypes.c_int32

    def _match(self):
        return (None, len(self.service), self.service, len(self.account), self.account)

    @staticmethod
    def _require(status):
        if status == _NOT_FOUND:
            raise ClientError("尚未连接因子账号，请调用 start_login。", "login_required")
        if status:
            raise ClientError("无法访问 macOS 登录钥匙串，请解锁后重试。", "keychain_unavailable")

    def read(self):
        size, data = _UINT(), _POINTER()
        status = self.security.SecKeychainFindGenericPassword(
            *self._match(), ctypes.byref(size), ctypes.byref(data), None)
        self._require(status)
        try:
            return ctypes.string_at(data, size.value)
        finally:
            self.security.SecKeychainItemFreeContent(None, data)

    @contextlib.contextmanager
    def _item(self):
        item = _POINTER()
        status = self.security.SecKeychainFindGenericPassword(*self._match(), None, None, ctypes.byref(item))
        if status != _NOT_FOUND:
            self._require(status)
        try:
            yield item if status == 0 else None
        finally:
            if item:
                self.core.CFRelease(item)

    def save(self, data):
        payload = ctypes.create_string_buffer(data)
        with self._item() as item:
            if item is None:
                status = self.security.SecKeychainAddGenericPassword(*self._match(), len(data), payload, None)
            else:
                status = self.security.SecKeychainItemModifyAttributesAndData(item, None, len(data), payload)
            self._require(status)

    def clear(self):
        with self._item() as item:
            if item is not None:
                self._require(self.security.SecKeychainItemDelete(item))
