"""Work overlapped on threads with its results kept in order: numpy, OpenColorIO and the
pipes to ffmpeg release the GIL, so threads run them at once."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

T = TypeVar("T")


def in_order(function: Callable[..., T], items: Iterable[tuple[Any, ...]], *, workers: int,
             ahead: int) -> Iterator[T]:
    """`function(*item)` for each item on `workers` threads, the results yielded in the
    items' order with at most `ahead` of them waiting. The items are taken on the caller's
    thread, as they are needed, so whatever makes them runs in order there."""
    with ThreadPoolExecutor(workers) as pool:
        pending: deque = deque()
        for item in items:
            pending.append(pool.submit(function, *item))
            while len(pending) > ahead or (pending and pending[0].done()):
                yield pending.popleft().result()
        while pending:
            yield pending.popleft().result()


def prefetched(items: Iterator[T], depth: int = 4) -> Iterator[T]:
    """`items` read ahead on one thread, `depth` at most, so reading a pipe overlaps the
    work done on each item; an error in reading is raised here, where its item was due."""
    end = object()
    with ThreadPoolExecutor(1) as reader:
        ahead = deque(reader.submit(next, items, end) for _ in range(depth))
        while (item := ahead.popleft().result()) is not end:
            ahead.append(reader.submit(next, items, end))
            yield item
