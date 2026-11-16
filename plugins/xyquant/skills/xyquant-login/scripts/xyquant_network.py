"""Read the public OAuth metadata of the XYQuant service. Python 3.9+."""

from dataclasses import dataclass
import errno
import http.client
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
from typing import Optional
import urllib.error
import urllib.request


HOST = "47.103.215.251"
AUTH_URL = f"https://{HOST}/factors/oauth/authorize"
DISCOVERY = "/.well-known/oauth-authorization-server"
TIMEOUT = 5
MAX_BODY = 64 * 1024


@dataclass(frozen=True)
class DiagnoseResult:
    mode: str
    interface: Optional[str]
    detail: str


def open_direct_socket(interface, timeout=8):
    """Connect only to XYQuant:443. On Windows the interface is a decimal index."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        if sys.platform == "darwin":
            # IP_BOUND_IF = 25 in Darwin netinet/in.h.
            sock.setsockopt(socket.IPPROTO_IP, getattr(socket, "IP_BOUND_IF", 25),
                            socket.if_nametoindex(interface))
        elif sys.platform == "win32":
            # Winsock IP_UNICAST_IF takes a DWORD in network byte order.
            sock.setsockopt(socket.IPPROTO_IP, getattr(socket, "IP_UNICAST_IF", 31),
                            int(interface).to_bytes(4, "big"))
        elif sys.platform.startswith("linux") and hasattr(socket, "SO_BINDTODEVICE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, os.fsencode(interface) + b"\0")
        else:
            raise OSError(errno.ENOPROTOOPT, "本机未提供指定网卡的连接接口。")
        sock.connect((HOST, 443))
        return sock
    except BaseException:
        sock.close()
        raise


def _mac_interfaces():
    """Intersect macOS's active networks with its list of hardware interfaces."""
    def output(*command):
        return subprocess.check_output(command, text=True, timeout=3, stderr=subprocess.DEVNULL)

    try:
        hardware = output("/usr/sbin/networksetup", "-listallhardwareports")
        active = output("/usr/sbin/scutil", "--nwi")
    except (OSError, subprocess.SubprocessError):
        return []
    devices = set(re.findall(r"^Device:\s*(en\d+)\s*$", hardware, re.MULTILINE))
    interfaces = re.search(r"^Network interfaces:[ \t]*(.*)$", active, re.MULTILINE)
    if not interfaces:
        return []
    return [name for name in interfaces[1].split() if name in devices]


def _windows_interfaces():
    # Numeric IDs avoid encoding and quoting issues with localized adapter names.
    command = ("Get-NetAdapter -Physical -ErrorAction Stop | "
               "Where-Object Status -eq 'Up' | Select-Object -ExpandProperty ifIndex")
    try:
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        ids = subprocess.check_output([str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
                                      text=True, encoding="ascii", errors="replace",
                                      timeout=6, stderr=subprocess.DEVNULL)
    except (KeyError, OSError, subprocess.SubprocessError):
        return []
    return [value for value in ids.split() if re.fullmatch(r"[1-9][0-9]{0,8}", value)]


def _linux_interfaces():
    # The device link is present for hardware-backed adapters, unlike tun/loopback.
    adapters = []
    for _, name in socket.if_nameindex():
        path = Path("/sys/class/net") / name
        try:
            if (path / "device").exists() and (path / "operstate").read_text().strip() == "up":
                adapters.append(name)
        except OSError:
            continue
    return adapters


def _active_hardware_interfaces():
    if sys.platform == "darwin":
        return _mac_interfaces()
    if sys.platform == "win32":
        return _windows_interfaces()
    if sys.platform.startswith("linux"):
        try:
            return _linux_interfaces()
        except OSError:
            pass
    return []


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_metadata(interface):
    context = ssl.create_default_context()
    if interface is None:
        opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=context))
        request = urllib.request.Request(f"https://{HOST}{DISCOVERY}", headers={"Accept": "application/json"})
        with opener.open(request, timeout=TIMEOUT) as response:
            return response.status, response.read(MAX_BODY + 1)

    with open_direct_socket(interface, TIMEOUT) as raw:
        with context.wrap_socket(raw, server_hostname=HOST) as tls:
            connection = http.client.HTTPSConnection(HOST, timeout=TIMEOUT, context=context)
            connection.sock = tls
            try:
                connection.request("GET", DISCOVERY, headers={"Accept": "application/json"})
                with connection.getresponse() as response:
                    return response.status, response.read(MAX_BODY + 1)
            finally:
                connection.close()


def _connection_error(error):
    if isinstance(error, urllib.error.URLError):
        error = error.reason
    if isinstance(error, (ssl.SSLCertVerificationError, ssl.CertificateError)):
        return "certificate", "授权服务的 HTTPS 证书未通过校验，请核对本机时间、证书信任链和服务配置。"
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "unavailable", "连接授权服务超时。"
    route_errors = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.ETIMEDOUT,
                    errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED, errno.EADDRNOTAVAIL}
    # Python builds on Windows may expose Winsock codes rather than POSIX errno.
    winsock_route_errors = {10049, 10050, 10051, 10052, 10053, 10054, 10060, 10061, 10065}
    if isinstance(error, OSError) and (error.errno in route_errors or
                                       getattr(error, "winerror", None) in winsock_route_errors):
        return "unavailable", "当前网络无法与授权服务建立连接。"
    return "configuration", "读取授权服务失败，请核对 HTTPS、代理及服务配置。"


def _probe(interface=None):
    try:
        status, body = _read_metadata(interface)
    except urllib.error.HTTPError as error:
        status, body = error.code, b""
        if error.fp is not None:
            error.close()
    except (OSError, http.client.HTTPException, ValueError) as error:
        if interface and isinstance(error, OSError) and (
                error.errno in (errno.EACCES, errno.EPERM, errno.ENOPROTOOPT, errno.ENODEV)
                or getattr(error, "winerror", None) in (10013, 10042)):
            return DiagnoseResult("unavailable", interface, "当前系统或账号无法为本次连接使用指定网卡。")
        mode, detail = _connection_error(error)
        return DiagnoseResult(mode, interface, detail)
    if status != 200:
        return DiagnoseResult("configuration", interface, f"公开授权发现接口返回 HTTP {status}，请核对服务配置。")
    try:
        metadata = json.loads(body) if len(body) <= MAX_BODY else None
    except (ValueError, RecursionError):
        metadata = None
    if not isinstance(metadata, dict) or metadata.get("authorization_endpoint") != AUTH_URL:
        return DiagnoseResult("configuration", interface, "公开元数据未提供预期的 XYQuant 授权地址，请核对插件版本和服务配置。")
    if interface:
        return DiagnoseResult("direct", interface, f"默认网络连接失败，经网卡 {interface} 直连已验证 XYQuant 授权服务。")
    return DiagnoseResult("default", None, "当前网络已通过 HTTPS 访问 XYQuant 授权服务。")


def diagnose(allow_direct=True):
    """Try the usual route first; optionally probe up to three active physical adapters."""
    default = _probe()
    if default.mode != "unavailable" or not allow_direct:
        return default
    detail = default.detail
    for interface in _active_hardware_interfaces()[:3]:
        candidate = _probe(interface)
        if candidate.mode != "unavailable":
            return candidate
        detail = candidate.detail
    return DiagnoseResult("unavailable", None, detail + " 本机未检测到可连接 XYQuant 的备用网卡。")
