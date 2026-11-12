from dataclasses import dataclass
from math import ceil
from typing import Self

PAGE_SIZE = 5


@dataclass(frozen=True, slots=True)
class Page:
    number: int
    pages: int

    @property
    def offset(self) -> int:
        return (self.number - 1) * PAGE_SIZE

    @classmethod
    def of(cls, total: int, number: int) -> Self:
        pages = max(1, ceil(total / PAGE_SIZE))
        return cls(number=min(max(number, 1), pages), pages=pages)


def page_number_of(index: int) -> int:
    return index // PAGE_SIZE + 1
