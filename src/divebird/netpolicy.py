"""網路位址政策：AI（MCP）發起的下載預設不可連到內網、本機或保留位址，避免被利用去存取路由器、NAS 或本機服務。

只檢查主機「解析後」的 IP（不用字串比對，十六進位、八進位或 IPv4 對應 IPv6 等寫法都會先被系統解析成真正的位址）。
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit


class BlockedAddress(ValueError):
    """目標主機是內網、本機或保留位址。"""


def _blocked_ip(text: str) -> bool:
    ip = ipaddress.ip_address(text.split("%", 1)[0])     # 去掉 IPv6 的 zone（fe80::1%eth0）
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def check_host(host: str) -> None:
    """主機解析出的任何一個位址不是公開網際網路位址時丟出 BlockedAddress；無法解析時丟出 OSError。"""
    if not host:
        raise BlockedAddress("網址沒有主機名稱")
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    for info in infos:
        address = info[4][0]
        if _blocked_ip(address):
            raise BlockedAddress(f"{host} 指向內網或本機位址（{address}）")


def check_url(url: str) -> None:
    check_host(urlsplit(url).hostname or "")


def block_private_redirects(response, *args, **kwargs):
    """requests 的 response hook：轉址目標是內網或本機位址時中止，不送出請求。"""
    if response.is_redirect:
        target = urljoin(response.url, response.headers.get("location", ""))
        try:
            check_url(target)
        except BlockedAddress as e:
            raise BlockedAddress(f"轉址到不允許的位址：{e}") from None
        except OSError:
            pass    # 解析不到就讓後續連線自然失敗
    return response
