# -*- coding: utf-8 -*-
"""自研 PAZ 数据层（零第三方依赖）：
  parse_pamt(0.pamt) -> 文件清单（path/offset/comp/flags）
  read_paz_file(entry) -> 按 flags 选 paz 文件 -> 读偏移 -> 解压/解密

格式（2026-10-30 实测，游戏 1.0.0.3392）：
  头32B + [magic4][paz_count u32][hash4+zero4][paz表 count×(hash4+size4+sep4)]
  [folder_size u32][folder区: varint parent + slen u8 + name]     ★ parent 是 LEB128
  [node_size u32][node区: 同构，路径片段树]                        ★ parent 是 LEB128
  [folder_count u32][folder记录 count×16 跳过]
  [file_count u32][文件记录: node_ref/paz_offset/comp/orig/flags 各u32 = 20B/条]
  [suffix 区: u32 字节长度 + 若干 '\0' 结尾的扩展名]

三条最易搞错、且旧代码全错在这三处（实测，别凭印象改）：
  1. folder 与 node 两个流都读作 `varint parent + u8 len + name`。
     按 u32 定长读会每项多吞字节 → 累积错位 → 报 node_size implausible。
  2. file 记录的 `node_ref` 是**该节点相对 node 区起点的偏移**，指向 name 字段
     之前的 parent 字段；不是"节点序号"。
  3. 回溯父节点：`parent_offset = node_ref - parent`。parent 存的是
     "往回跳的字节距离"（= 父节点全长 + 2），不是父节点序号。
     按序号理解会拼出 `gamedata/_0circuit_rtifact__0circuit...` 这种残骸。

  flags 实为两个 u16 的合并读法（与 JMM 的 '<IIIIHH' 布局字节级等价）：
     低 u16          = paz_index（第几个 .paz）
     (flags>>16)&0xF = comp_type（0 明文 / 2 LZ4 / 4 zlib）
     flags>>24       = 后缀号（1-based，索引进 suffix 区）
  后缀是路径的一部分：`gamedata/dropsetinfo` + `.staticinfobody`。
  实测 dropsetinfo 的两个同节点条目正靠后缀区分（body=8 / header=9）。
"""
import sys, io, os, struct, zlib
# 库模块不该无条件改宿主 stdout：打包成窗口程序（pythonw/--windowed）时
# sys.stdout 是 None，直接 .buffer 会崩（"'NoneType' object has no attribute 'buffer'"）。
# 重包前先 flush —— 否则把调用方已缓冲的输出冲掉（排查时表现为"前面的 print 凭空消失"）。
if sys.stdout is not None and hasattr(sys.stdout, 'buffer'):
    try:
        sys.stdout.flush()
    except Exception:
        pass
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
# ⚠️ 这里曾有 sys.path.insert(0, r'D:/新建文件夹/_nojunk_snap') —— 复制遗留的绝对路径。
# 它把旧目录插到搜索路径最前，导致 import game_data 拿到旧版（无 stdout 保护），
# 打包时也会把旧版打进去 → exe 一启动就崩。绝不要写死绝对路径。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cd_crypto

_MAX_PAZ = 4096


class PazEntry:
    __slots__ = ('path', 'offset', 'comp_size', 'orig_size', 'flags', 'paz_index', 'comp_type')

    def __init__(self, path, offset, comp_size, orig_size, flags):
        self.path = path
        self.offset = offset
        self.comp_size = comp_size
        self.orig_size = orig_size
        self.flags = flags
        # flags 实为两个 u16 的合并读法（与 JMM 的 '<IIIIHH' 布局字节级等价，
        # 已经第三方交叉验证）：低 u16 = paz_index，高 u16 的低 4 位 = comp_type
        self.paz_index = flags & 0xFFFF
        self.comp_type = (flags >> 16) & 0x0F


def _uleb128(buf, off, what='uleb128'):
    """读 LEB128 变长无符号整数，返回 (值, 新偏移)。越界/超长抛 ValueError。

    新版 pamt 把 parent 从 4 字节定长改成了 LEB128：小值（0/3/10…）占 1 字节，
    大于 127 的值（如 191）占 2 字节。按定长 4 字节读会让每项多吞字节，
    累积偏移错乱，最终把字符串当成 node_size 读出天价数字。
    """
    val = 0
    shift = 0
    start = off
    while True:
        if off + 1 > len(buf):
            raise ValueError('pamt truncated in %s (off=%d, len=%d)'
                             % (what, off, len(buf)))
        b = buf[off]
        off += 1
        val |= (b & 0x7f) << shift
        if not (b & 0x80):
            return val, off
        shift += 7
        if off - start > 8:                 # 防御：变长字段异常膨胀
            raise ValueError('pamt %s overlong (off=%d)' % (what, start))


def _read_name_stream(data, start, end, as_uleb, what, key_is_offset=True):
    """读 folder/node 名字流：重复 `parent + u8 len + name`。

    as_uleb=True  → parent 是 LEB128（新版）
    as_uleb=False → parent 是 4 字节定长（旧版）
    key_is_offset → 用"相对流起点的偏移"作 key（新版 node_ref 就是它）
                    否则用出现序号（旧版语义）
    返回 (items, pos, ok)。ok = 是否正好读完到 end —— 这是新旧格式的判定依据，
    也是 §2.1 那 4 条结构完整性断言的头两条。
    """
    items = []
    p = start
    try:
        while p < end:
            if as_uleb:
                parent, p2 = _uleb128(data, p, what + ' parent')
            else:
                if p + 4 > end:
                    return items, p, False
                parent = struct.unpack_from('<I', data, p)[0]
                p2 = p + 4
            if p2 + 1 > len(data):
                return items, p, False
            slen = data[p2]
            if p2 + 1 + slen > len(data):
                return items, p, False
            name = data[p2 + 1:p2 + 1 + slen].decode('utf-8', 'replace')
            key = (p - start) if key_is_offset else len(items)
            items.append((key, parent, name))
            p = p2 + 1 + slen
    except ValueError:
        return items, p, False
    return items, p, (p == end)


def _read_suffixes(data, suf_off):
    """读文件末尾的扩展名区：`u32 字节长度` + 若干 '\\0' 结尾的串。

    ⚠️ 首字段是**字节长度**（实测 204），不是条数 —— 别按 count 用。
    实测 0008 包里是 15 个后缀：.xml / .binarystring / .staticinfobody /
    .staticinfoheader / .nav / .road / .roadsector / .roadidx / .save …
    """
    if suf_off + 4 > len(data):
        return []
    blob = struct.unpack_from('<I', data, suf_off)[0]
    q = suf_off + 4
    end = len(data)
    if 0 < blob <= len(data) - q:
        end = q + blob
    out = []
    while q < end:
        if data[q] == 0:
            q += 1
            continue
        e = data.find(b'\x00', q, end)
        if e < 0:
            e = end
        out.append(data[q:e].decode('ascii', 'replace'))
        q = e + 1
    return out


def parse_pamt(pamt_path):
    # ：原来裸 open(...).read() 不关句柄
    with open(pamt_path, 'rb') as _f:
        data = _f.read()
    if len(data) < 32:
        raise ValueError('pamt too short')

    def _u32(off, what):
        # 自审 P1-3（low-rev）：所有 unpack 带边界检查——
        # 截断/篡改的 pamt 必须给可读错误，不是裸 struct.error
        if off + 4 > len(data):
            raise ValueError('pamt truncated at %s (off=%d, len=%d)'
                             % (what, off, len(data)))
        return struct.unpack_from('<I', data, off)[0]

    off = 4                                     # magic
    paz_count = _u32(off, 'paz_count'); off += 4
    if paz_count > _MAX_PAZ:
        raise ValueError('paz_count implausible: %d' % paz_count)
    off += 8                                    # hash + zero
    for i in range(paz_count):                  # paz 表
        off += 8
        if i < paz_count - 1:
            off += 4

    # ── folder 区：先按 varint 试，对不齐再按 u32 定长（旧版兼容）──────
    folder_size = _u32(off, 'folder_size'); off += 4
    if folder_size > len(data):
        raise ValueError('pamt folder_size implausible: %d' % folder_size)
    folder_end = off + folder_size
    folders, pos, ok = _read_name_stream(data, off, folder_end, True, 'folder',
                                         key_is_offset=True)
    if not ok:
        folders, pos, ok = _read_name_stream(data, off, folder_end, False, 'folder',
                                             key_is_offset=False)
    if not ok:
        raise ValueError('pamt folder 流对不齐（%d → %d，期望 %d）'
                         % (off, pos, folder_end))
    # 根文件夹：parent == 0 的那条。实测 0008 包只有 'gamedata' 一个。
    folder_prefix = ''
    for _k, parent, name in folders:
        if parent == 0:
            folder_prefix = name
            break

    # ── node 区 ────────────────────────────────────────────────────
    node_size = _u32(pos, 'node_size')
    if node_size > len(data):
        raise ValueError('pamt node_size implausible: %d' % node_size)
    node_start = pos + 4
    node_end = node_start + node_size
    nodes, npos, ok2 = _read_name_stream(data, node_start, node_end, True, 'node',
                                         key_is_offset=True)
    mode = 'varint'
    if not ok2:
        nodes, npos, ok2 = _read_name_stream(data, node_start, node_end, False, 'node',
                                             key_is_offset=False)
        mode = 'u32'
    if not ok2:
        raise ValueError('pamt node 流对不齐（%d → %d，期望 %d）'
                         % (node_start, npos, node_end))
    nmap = {k: (parent, name) for k, parent, name in nodes}

    def build_path(ref):
        # parent 语义按格式分支：
        #   varint（新版）→ parent 是"往回跳的字节距离"，cur -= parent
        #   u32（旧版）   → parent 是父节点序号，cur = parent
        parts = []
        cur = ref
        seen = set()
        while cur in nmap and cur not in seen and len(parts) < 64:
            seen.add(cur)
            parent, name = nmap[cur]
            parts.append(name)
            if parent == 0:
                break
            cur = (cur - parent) if mode == 'varint' else parent
        return ''.join(reversed(parts))

    # ── folder 记录区（跳过）：新版 16B/条，旧版 24B/条 ──────────────
    folder_count = _u32(node_end, 'folder_count')
    rec = node_end + 4 + folder_count * 16
    file_count = _u32(rec, 'file_count')
    # 兜底：若 16B 算出的位置读出的 file_count 不合理，改按 24B 再试一次
    if not (0 < file_count <= (len(data) - rec) // 20):
        rec = node_end + 4 + folder_count * 24
        file_count = _u32(rec, 'file_count')
    if not (0 < file_count <= (len(data) - rec) // 20):
        raise ValueError('pamt file_count implausible: %d' % file_count)

    suffixes = _read_suffixes(data, rec + 4 + file_count * 20)

    entries = []
    for i in range(file_count):
        o = rec + 4 + 20 * i
        if o + 20 > len(data):
            break
        node_ref, paz_off, comp, orig, flags = struct.unpack_from('<IIIII', data, o)
        path = build_path(node_ref)
        # 后缀号在 flags>>24（1-based）。越界时不硬拼——宁可缺后缀也不要脏路径。
        sid = flags >> 24
        if 1 <= sid <= len(suffixes):
            path += suffixes[sid - 1]
        full = (folder_prefix + '/' + path) if folder_prefix else path
        entries.append(PazEntry(full, paz_off, comp, orig, flags))
    return entries


def read_paz_file(entries, want, game_dir, pamt_stem='0'):
    """按路径找条目 -> 读对应 paz -> 解压/解密 -> 返回明文 bytes。
    加密规则（实测）：.paloc 加密；dropsetinfo 等 LZ4 文件不加密。
    LZ4/zlib 链用 try 自适应（先不解密，失败再解密，size 校验兜底）。"""
    e = [x for x in entries if x.path == want or x.path.endswith('/' + want)]
    #  自审 P2-4：endswith 要补路径分隔符边界——
    # 'character.paloc' 不能误中 'xcharacter.paloc'
    if not e:
        raise KeyError('not in pamt: %s' % want)
    if len(e) > 1:
        exact = [x for x in e if x.path == want]
        e = exact if exact else e[:1]
    e = e[0]
    paz_num = int(pamt_stem) + e.paz_index
    paz_path = os.path.join(game_dir, '%d.paz' % paz_num)
    with open(paz_path, 'rb') as f:
        f.seek(e.offset)
        raw = f.read(e.comp_size)
    #  自审 P2-5：短读不静默——截断的 paz 必须报错而不是产脏数据
    if len(raw) != e.comp_size:
        raise ValueError('paz 短读: %s 期望 %d 实读 %d'
                         % (e.path, e.comp_size, len(raw)))

    def _decomp(d):
        if e.comp_type == 2:
            return cd_crypto.lz4_block_decompress(d, e.orig_size)
        if e.comp_type == 4:
            #  自审 P1：zlib 同族内存炸弹——裸 decompress 无上限，
            # 实测 200KB 压缩包解出 200MB。限长到声明值。
            # ：原实现写成 `d.decompress(d if False else data_in, ...)`
            # ——形参 d 被就地改写成 decompressobj，`d if False` 是永远不走的死分支，
            # 实际吃的是闭包变量 data_in。行为凑巧正确，但读起来像 bug，
            # 且依赖「data_in 在调用前已赋值」这个隐式时序。改写为显式传参。
            zo = zlib.decompressobj()
            out = zo.decompress(d, e.orig_size + 1)
            if len(out) > e.orig_size:
                raise ValueError('zlib: output overrun (declared %d)' % e.orig_size)
            # ：与 LZ4 同口径——不光卡上界，长度必须精确等于声明值
            if len(out) != e.orig_size:
                raise ValueError('zlib: size mismatch %d != %d'
                                 % (len(out), e.orig_size))
            return out
        return d

    if e.comp_type == 0 and e.path.endswith('.paloc'):
        return cd_crypto.decrypt(raw, e.path)          # paloc：必加密
    try:
        return _decomp(raw)                            # 先试不加密（LZ4 size 校验兜底）
    except cd_crypto.Lz4DeclaredSizeError:
        #  ：这是**内存炸弹守卫**报错（声明的解压后大小荒谬），
        # 与文件内容无关 —— 解密重试拿到的还是同一个 orig_size，救不回来。
        # 旧代码裸 except 把它和"这文件其实加密了"混为一谈，静默转成重试，
        # 守卫的报错语义被吞掉（防护本身没失效，重试路径同样有守卫，是诊断失真）。
        raise
    except Exception as _ex_plain:
        # ⚠️ 其余 LZ4 报错**不能**跟着直接抛：实测 0008 包 comp_type==2 条目
        #    抽样 400 条有 6 条（1.5%）明文解不开、必须走解密才对。数据相关的
        #    LZ4 报错（bad offset / size mismatch / overrun）与"文件加密了"
        #    在字节层面无法区分，只能两条路都试，否则会真实读坏 1.5% 的文件。
        try:
            return _decomp(cd_crypto.decrypt(raw, e.path))  # 再试解密
        except Exception as _ex_enc:
            # 两条路都失败时把明文路径的报错挂成 __cause__：排查时能同时看到
            # "明文路径怎么错的"和"解密路径怎么错的"，不再被静默吞掉。
            raise _ex_enc from _ex_plain


if __name__ == '__main__':
    G = r'F:/SteamLibrary/steamapps/common/Crimson Desert/0008'
    es = parse_pamt(os.path.join(G, '0.pamt'))
    print('pamt 条目:', len(es))
    out = read_paz_file(es, 'gamedata/dropsetinfo.staticinfobody', G)
    van = open(r'D:/新建文件夹/NoJunk-Manager/data/vanilla_dropsetinfo.bin', 'rb').read()
    print('dropsetinfo 解出 %d 字节，与已知原版一致: %s' % (len(out), out == van))
    # 顺手验 characterinfo（paz 里的敌人定义库）
    ci = read_paz_file(es, 'characterinfo.pabgb', G)
    ref = open(r'F:/SteamLibrary/steamapps/common/Crimson Desert/_MOD工具与资料/数据文件/_characterinfo.bin', 'rb').read()
    print('characterinfo.pabgb 解出 %d 字节，与参考副本一致: %s' % (len(ci), ci == ref))
    # 中文包也走同一链路
    G32 = r'F:/SteamLibrary/steamapps/common/Crimson Desert/0032'
    es32 = parse_pamt(os.path.join(G32, '0.pamt'))
    pd = read_paz_file(es32, 'gamedata/character.paloc', G32)
    print('0032 character.paloc 头部:', pd[:6])
