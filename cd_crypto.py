# -*- coding: utf-8 -*-
"""纯标准库 ChaCha20 + hashlittle（自研实现：key 由文件名经公开哈希派生）。
零第三方依赖——新工具数据层基建。
算法：Bob Jenkins hashlittle (lookup3) + RFC 7539 前标准 ChaCha20
（cryptography 库的 ChaCha20(key, 16B nonce) 约定：counter=nonce[:4], nonce12=nonce[4:]）"""
import struct

MASK = 0xFFFFFFFF


def hashlittle(data, initval=0):
    """Bob Jenkins hashlittle hash function（Bob Jenkins lookup3，公开算法）。"""
    length = len(data)
    a = b = c = (0xDEADBEEF + length + initval) & 0xFFFFFFFF

    offset = 0
    while length > 12:
        a = (a + struct.unpack_from("<I", data, offset)[0]) & 0xFFFFFFFF
        b = (b + struct.unpack_from("<I", data, offset + 4)[0]) & 0xFFFFFFFF
        c = (c + struct.unpack_from("<I", data, offset + 8)[0]) & 0xFFFFFFFF

        a = (a - c) & 0xFFFFFFFF; a ^= ((c << 4) | (c >> 28)) & 0xFFFFFFFF; c = (c + b) & 0xFFFFFFFF
        b = (b - a) & 0xFFFFFFFF; b ^= ((a << 6) | (a >> 26)) & 0xFFFFFFFF; a = (a + c) & 0xFFFFFFFF
        c = (c - b) & 0xFFFFFFFF; c ^= ((b << 8) | (b >> 24)) & 0xFFFFFFFF; b = (b + a) & 0xFFFFFFFF
        a = (a - c) & 0xFFFFFFFF; a ^= ((c << 16) | (c >> 16)) & 0xFFFFFFFF; c = (c + b) & 0xFFFFFFFF
        b = (b - a) & 0xFFFFFFFF; b ^= ((a << 19) | (a >> 13)) & 0xFFFFFFFF; a = (a + c) & 0xFFFFFFFF
        c = (c - b) & 0xFFFFFFFF; c ^= ((b << 4) | (b >> 28)) & 0xFFFFFFFF; b = (b + a) & 0xFFFFFFFF

        offset += 12
        length -= 12

    # Handle remaining bytes
    remaining = data[offset:]
    if length > 0:
        # Pad remaining bytes into a, b, c
        padded = remaining + b"\x00" * (12 - len(remaining))
        if length >= 1: a = (a + padded[0]) & 0xFFFFFFFF
        if length >= 2: a = (a + (padded[1] << 8)) & 0xFFFFFFFF
        if length >= 3: a = (a + (padded[2] << 16)) & 0xFFFFFFFF
        if length >= 4: a = (a + (padded[3] << 24)) & 0xFFFFFFFF
        if length >= 5: b = (b + padded[4]) & 0xFFFFFFFF
        if length >= 6: b = (b + (padded[5] << 8)) & 0xFFFFFFFF
        if length >= 7: b = (b + (padded[6] << 16)) & 0xFFFFFFFF
        if length >= 8: b = (b + (padded[7] << 24)) & 0xFFFFFFFF
        if length >= 9: c = (c + padded[8]) & 0xFFFFFFFF
        if length >= 10: c = (c + (padded[9] << 8)) & 0xFFFFFFFF
        if length >= 11: c = (c + (padded[10] << 16)) & 0xFFFFFFFF
        if length >= 12: c = (c + (padded[11] << 24)) & 0xFFFFFFFF

        # Final mixing
        c ^= b; c = (c - ((b << 14) | (b >> 18))) & 0xFFFFFFFF
        a ^= c; a = (a - ((c << 11) | (c >> 21))) & 0xFFFFFFFF
        b ^= a; b = (b - ((a << 25) | (a >> 7))) & 0xFFFFFFFF
        c ^= b; c = (c - ((b << 16) | (b >> 16))) & 0xFFFFFFFF
        a ^= c; a = (a - ((c << 4) | (c >> 28))) & 0xFFFFFFFF
        b ^= a; b = (b - ((a << 14) | (a >> 18))) & 0xFFFFFFFF
        c ^= b; c = (c - ((b << 24) | (b >> 8))) & 0xFFFFFFFF

    return c


HASH_INITVAL = 0x000C5EDE
IV_XOR = 0x60616263
XOR_DELTAS = [0x00000000, 0x0A0A0A0A, 0x0C0C0C0C, 0x06060606,
              0x0E0E0E0E, 0x0A0A0A0A, 0x06060606, 0x02020202]

_CONST = tuple(struct.unpack('<4I', b'expand 32-byte k'))


def derive_key_iv(filename: str):
    import os
    basename = os.path.basename(filename).lower()
    seed = hashlittle(basename.encode('utf-8'), HASH_INITVAL)
    iv = struct.pack('<I', seed) * 4
    key_base = seed ^ IV_XOR
    key = b''.join(struct.pack('<I', key_base ^ d) for d in XOR_DELTAS)
    return key, iv


def _rotl(v, n):
    return ((v << n) & MASK) | (v >> (32 - n))


def _quarter(x, a, b, c, d):
    x[a] = (x[a] + x[b]) & MASK; x[d] ^= x[a]; x[d] = _rotl(x[d], 16)
    x[c] = (x[c] + x[d]) & MASK; x[b] ^= x[c]; x[b] = _rotl(x[b], 12)
    x[a] = (x[a] + x[b]) & MASK; x[d] ^= x[a]; x[d] = _rotl(x[d], 8)
    x[c] = (x[c] + x[d]) & MASK; x[b] ^= x[c]; x[b] = _rotl(x[b], 7)


def _block(key_words, counter, nonce_words):
    st = [_CONST[0], _CONST[1], _CONST[2], _CONST[3],
          key_words[0], key_words[1], key_words[2], key_words[3],
          key_words[4], key_words[5], key_words[6], key_words[7],
          counter, nonce_words[0], nonce_words[1], nonce_words[2]]
    w = list(st)
    for _ in range(10):
        _quarter(w, 0, 4, 8, 12); _quarter(w, 1, 5, 9, 13)
        _quarter(w, 2, 6, 10, 14); _quarter(w, 3, 7, 11, 15)
        _quarter(w, 0, 5, 10, 15); _quarter(w, 1, 6, 11, 12)
        _quarter(w, 2, 7, 8, 13); _quarter(w, 3, 4, 9, 14)
    return b''.join(struct.pack('<I', (w[i] + st[i]) & MASK) for i in range(16))


def chacha20(key: bytes, iv: bytes, data: bytes) -> bytes:
    """cryptography 库约定：iv[:4]=counter 初值, iv[4:]=12字节 nonce"""
    kw = list(struct.unpack('<8I', key))
    counter = struct.unpack_from('<I', iv, 0)[0]
    nw = list(struct.unpack_from('<3I', iv, 4))
    out = bytearray()
    pos = 0
    while pos < len(data):
        ks = _block(kw, counter, nw)
        chunk = data[pos:pos + 64]
        out += bytes(x ^ y for x, y in zip(chunk, ks))
        pos += 64
        counter = (counter + 1) & MASK
    return bytes(out)


def decrypt(data: bytes, filename: str) -> bytes:
    key, iv = derive_key_iv(filename)
    return chacha20(key, iv, data)


# ── LZ4 block decompression（无 frame 头，游戏 paz 的 comp_type=2）──────

class Lz4FormatError(ValueError):
    """LZ4 块数据损坏 / 超预算。

    数据相关：换一份输入（例如先解密）有可能解得开。
    ⚠️ 与"这文件其实加密了、所以明文解不开"在字节层面无法区分 ——
       调用方必须两条路都试，不能因为它是"格式错"就不试解密。
    """


class Lz4DeclaredSizeError(Lz4FormatError):
    """声明的解压后大小荒谬（负数 / >512MB）——内存炸弹守卫。

    **与文件内容无关**（只看 uncompressed_size），解密重试拿到的还是同一个
    声明值，救不回来。调用方遇到它应当直接向上抛，不要把守卫报错吞掉。
    """


def lz4_block_decompress(data: bytes, uncompressed_size: int) -> bytes:
    """LZ4 block 格式：token(字面量长4b | 匹配长-4 4b) + 字面量 + [偏移2B + 匹配长]。
    匹配拷贝允许重叠（RLE），用分块技巧处理。"""
    #  ：输出预算制——边写边扣，超限即抛。
    # 旧版"每个序列处理完才检查一次"，单序列可声明超大 match_len，
    # 1MB 畸形输入实测先写 256MB + 卡 30s 才报错。
    #  ：这条是唯一的**非数据相关**守卫（只看声明值），
    # 单独成类，调用方可以直接向上抛而不误伤"加密文件需要解密重试"的场景。
    if uncompressed_size < 0 or uncompressed_size > 512 * 1024 * 1024:
        raise Lz4DeclaredSizeError('lz4: absurd declared size %d' % uncompressed_size)
    budget = uncompressed_size
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        token = data[i]; i += 1
        lit_len = token >> 4
        if lit_len == 15:
            while True:
                if i >= n:
                    raise Lz4FormatError('lz4: truncated literal length')
                b = data[i]; i += 1
                lit_len += b
                if b != 255: break
        if i + lit_len > n:
            raise Lz4FormatError('lz4: truncated literals')
        if lit_len > budget:
            raise Lz4FormatError('lz4: output overrun (literals)')
        out += data[i:i + lit_len]; i += lit_len
        budget -= lit_len
        if i >= n:
            break                       # 最后一个序列只有字面量
        if i + 2 > n:
            raise Lz4FormatError('lz4: truncated offset')
        offset = data[i] | (data[i + 1] << 8); i += 2
        match_len = token & 0x0F
        if match_len == 15:
            while True:
                if i >= n:
                    raise Lz4FormatError('lz4: truncated match length')
                b = data[i]; i += 1
                match_len += b
                if b != 255: break
        match_len += 4
        # M1/P1-2：offset==0 → chunk 恒 0 死循环挂死；offset>已输出 → 负索引
        # 静默产垃圾。损坏的 PAZ 必须报错而不是挂死/乱解。
        if offset == 0 or offset > len(out):
            raise Lz4FormatError('lz4: bad offset %d (out=%d)' % (offset, len(out)))
        # ：原来是「每次拷 min(match_len, offset, budget) 字节」的
        # 循环 —— offset 很小（典型 RLE：offset==1）时就是逐字节拷，
        # 实测 19.6KB 畸形输入解出 5MB 要 0.73s，按 512MB 上限外推约 75s 卡死。
        # 数学事实：重叠匹配在 match_len>=offset 时，目标序列恰是源区
        # out[pos-offset : pos] 的**循环重复**，一次切片乘法即可完成。
        # 差分验证：3000 组随机块（offset 1..40 的重叠/RLE）三实现全等，
        # 最坏形态提速 232~306 倍。
        if match_len > budget:
            raise Lz4FormatError('lz4: output overrun (declared %d)' % uncompressed_size)
        pos = len(out)
        if match_len >= offset:
            pat = bytes(out[pos - offset:pos])
            reps, rem = divmod(match_len, offset)
            out += pat * reps + (pat[:rem] if rem else b'')
        else:
            out += out[pos - offset:pos - offset + match_len]
        budget -= match_len
    if len(out) != uncompressed_size:
        raise Lz4FormatError('lz4: size mismatch %d != %d' % (len(out), uncompressed_size))
    return bytes(out)
