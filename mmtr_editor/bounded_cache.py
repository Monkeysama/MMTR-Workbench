"""会话／任务独占的 LRU；同时限制条数和保留字节，不缓存可变编辑状态。"""
from collections import OrderedDict


class ByteLRU:
    """调用方提供含键和值的内存权重；值须只读，缓存不跨线程共享。"""
    def __init__(self, entries, byte_limit):
        self.entries, self.byte_limit = entries, byte_limit
        self._items = OrderedDict()
        self.byte_size = 0

    def get(self, key):
        item = self._items.get(key)
        if item is None:
            return None
        self._items.move_to_end(key)
        return item[0]

    def put(self, key, value, size):
        previous = self._items.pop(key, None)
        if previous is not None:
            self.byte_size -= previous[1]
        if size > self.byte_limit or self.entries < 1:
            return
        while self._items and (len(self._items) >= self.entries or self.byte_size + size > self.byte_limit):
            _, (_, removed) = self._items.popitem(last=False)
            self.byte_size -= removed
        self._items[key] = value, size
        self.byte_size += size

    def clear(self):
        self._items.clear()
        self.byte_size = 0

    def __len__(self):
        return len(self._items)

    def __deepcopy__(self, memo):
        # 编辑事务不复制不可变编译产物，仍恢复独立的淘汰顺序和容量计数。
        result = type(self)(self.entries, self.byte_limit)
        result._items = self._items.copy()
        result.byte_size = self.byte_size
        return result
