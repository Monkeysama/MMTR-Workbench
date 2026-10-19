"""MMTR名称与Shader尾标使用的纯Python哈希；无文件、编译器或GPU依赖。"""
M32 = 0xffffffff
M64 = 0xffffffffffffffff


# 按指定位宽循环左移，中间值始终限制在该位宽。
def rol(value, count, bits):
    return ((value << count) | (value >> (bits - count))) & ((1 << bits) - 1)


# 计算名称描述符的32位MurmurHash3；编码与seed由格式调用方指定。
def murmur3_x86_32(data, seed):
    h = seed
    end = len(data) - len(data) % 4
    for i in range(0, end, 4):
        k = int.from_bytes(data[i:i + 4], 'little')
        k = rol(k * 0xcc9e2d51 & M32, 15, 32) * 0x1b873593 & M32
        h = (rol(h ^ k, 13, 32) * 5 + 0xe6546b64) & M32
    if end != len(data):
        k = int.from_bytes(data[end:], 'little')
        h ^= rol(k * 0xcc9e2d51 & M32, 15, 32) * 0x1b873593 & M32
    h ^= len(data)
    h ^= h >> 16
    h = h * 0x85ebca6b & M32
    h ^= h >> 13
    h = h * 0xc2b2ae35 & M32
    return (h ^ (h >> 16)) & M32


# 计算完整Shader载荷的64位xxHash；不包含尾部校验字本身。
def xxh64(data, seed=0):
    p1, p2, p3, p4, p5 = 11400714785074694791, 14029467366897019727, 1609587929392839161, 9650029242287828579, 2870177450012600261
    def rnd(acc, value):
        return rol((acc + value * p2) & M64, 31, 64) * p1 & M64
    i = 0
    if len(data) >= 32:
        vs = [(seed + p1 + p2) & M64, (seed + p2) & M64, seed & M64, (seed - p1) & M64]
        while i <= len(data) - 32:
            for n in range(4):
                vs[n] = rnd(vs[n], int.from_bytes(data[i + n * 8:i + n * 8 + 8], 'little'))
            i += 32
        h = sum(rol(v, r, 64) for v, r in zip(vs, [1, 7, 12, 18])) & M64
        for v in vs:
            h = ((h ^ rnd(0, v)) * p1 + p4) & M64
    else:
        h = (seed + p5) & M64
    h = (h + len(data)) & M64
    while i <= len(data) - 8:
        h = (rol(h ^ rnd(0, int.from_bytes(data[i:i + 8], 'little')), 27, 64) * p1 + p4) & M64
        i += 8
    if i <= len(data) - 4:
        h = (rol(h ^ (int.from_bytes(data[i:i + 4], 'little') * p1 & M64), 23, 64) * p2 + p3) & M64
        i += 4
    while i < len(data):
        h = rol(h ^ (data[i] * p5 & M64), 11, 64) * p1 & M64
        i += 1
    h ^= h >> 33
    h = h * p2 & M64
    h ^= h >> 29
    h = h * p3 & M64
    return (h ^ (h >> 32)) & M64
