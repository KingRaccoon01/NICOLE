"""
Nicole — Ortak HTTP istemci yardımcısı

İki ayrı ağ sorununa karşı önlem alır:

1) SSL oturum çakışması — bazı Windows makinelerinde bir antivirüs/güvenlik
   yazılımının HTTPS trafiğini araya girip incelemesi yüzünden istekler
   "[SSL: INVALID_SESSION_ID] invalid session id" hatasıyla başarısız olabiliyor.

2) IPv4/IPv6 uyumsuzluğu — bazı ağlarda (özellikle IPv6'nın birincil, IPv4'ün
   arızalı/filtreli olduğu ağlarda) tarayıcılar "Happy Eyeballs" tekniğiyle
   ikisini paralel deneyip çalışanı kullanır, ama Python'un httpx kütüphanesi
   bunu yapmaz — DNS'in verdiği ilk adresi (genelde IPv4) dener, o çalışmazsa
   IPv6'ya hiç geçmeden "ConnectTimeout" ile başarısız olur. Bu, "tarayıcıda
   çalışıyor ama Python'da çalışmıyor" şikayetinin tipik nedenidir.
"""
from __future__ import annotations
import socket
import ssl

import httpx


def _build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    # TLS 1.3 oturum biletlerini (session tickets) kapat — SSL inceleme yapan
    # yazılımlarla yaşanan "invalid session id" uyuşmazlığının yaygın nedeni budur.
    if hasattr(ssl, "OP_NO_TICKET"):
        ctx.options |= ssl.OP_NO_TICKET
    # TLS'i 1.2'ye sabitle. Bazı SSL inceleme/antivirüs yazılımları TLS 1.3'ün
    # yeni özellikleriyle (session tickets, 0-RTT, encrypted extensions) tam
    # uyumlu değildir ve "invalid session id" gibi hatalara yol açar — bunları
    # tamamen devre dışı bırakan TLS 1.2, bu tür yazılımlarla daha uyumludur.
    try:
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    except (AttributeError, ValueError):
        pass  # Çok eski Python/OpenSSL sürümlerinde bu özellik olmayabilir, sorun değil
    return ctx


_ssl_context = _build_ssl_context()

_patched = False


def _patch_dns_prefer_ipv6() -> None:
    """
    socket.getaddrinfo'yu, IPv6 sonuçlarını IPv4'ten ÖNCE döndürecek şekilde
    yamalar (process genelinde, sadece bir kez uygulanır). "Happy Eyeballs"
    olmadan çalışan httpx/httpcore, DNS'in ilk verdiği adresi dener — IPv6'yı
    öne alarak, IPv6'nın çalışıp IPv4'ün çalışmadığı ağlarda ilk denemenin
    başarılı olmasını sağlıyoruz. IPv6 hiç yoksa (çoğu ağda durum budur) bu
    yama hiçbir şeyi değiştirmez, tamamen zararsızdır.
    """
    global _patched
    if _patched:
        return
    _patched = True

    original_getaddrinfo = socket.getaddrinfo

    def _getaddrinfo_ipv6_first(host, port, family=0, type=0, proto=0, flags=0):
        results = original_getaddrinfo(host, port, family, type, proto, flags)
        ipv6_results = [r for r in results if r[0] == socket.AF_INET6]
        ipv4_results = [r for r in results if r[0] != socket.AF_INET6]
        return ipv6_results + ipv4_results

    socket.getaddrinfo = _getaddrinfo_ipv6_first


_patch_dns_prefer_ipv6()


def new_client(timeout: float = 30.0) -> httpx.AsyncClient:
    """
    Her çağrıda taze bir istemci döner (bağlantı havuzu/oturum yeniden
    kullanımını en aza indirir — bu da SSL oturum çakışması ihtimalini azaltır).
    """
    return httpx.AsyncClient(timeout=timeout, verify=_ssl_context)