"""Typed, domain-separated Merkle commitments and inclusion proofs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .serialization import digest


@dataclass(frozen=True)
class MerkleLeaf:
    leaf_type: str
    index: int
    value: Any

    def hash(self) -> str:
        return digest("merkle-leaf/v1", self)


@dataclass(frozen=True)
class MerkleProof:
    index: int
    leaf_count: int
    siblings: tuple[tuple[str, str], ...]


def _parent(left: str, right: str) -> str:
    return digest("merkle-node/v1", {"left": left, "right": right})


class MerkleTree:
    def __init__(self, leaves: Iterable[MerkleLeaf]):
        self.leaves = tuple(leaves)
        if any(leaf.index != i for i, leaf in enumerate(self.leaves)):
            raise ValueError("Merkle leaf indices must be contiguous and ordered")
        level = [leaf.hash() for leaf in self.leaves]
        self._levels: list[list[str]] = [level]
        while len(level) > 1:
            level = [_parent(level[i], level[i + 1] if i + 1 < len(level) else level[i])
                     for i in range(0, len(level), 2)]
            self._levels.append(level)

    @property
    def root(self) -> str:
        return self._levels[-1][0] if self.leaves else digest("merkle-empty/v1", {})

    def prove(self, index: int) -> MerkleProof:
        if index < 0 or index >= len(self.leaves):
            raise IndexError("leaf index out of range")
        siblings: list[tuple[str, str]] = []
        position = index
        for level in self._levels[:-1]:
            if position % 2:
                siblings.append(("left", level[position - 1]))
            else:
                sibling = level[position + 1] if position + 1 < len(level) else level[position]
                siblings.append(("right", sibling))
            position //= 2
        return MerkleProof(index, len(self.leaves), tuple(siblings))


def verify_inclusion(leaf: MerkleLeaf, proof: MerkleProof, expected_root: str) -> bool:
    if proof.leaf_count <= 0 or proof.index != leaf.index or proof.index >= proof.leaf_count:
        return False
    if len(proof.siblings) != (proof.leaf_count - 1).bit_length():
        return False
    current = leaf.hash()
    position, width = proof.index, proof.leaf_count
    for side, sibling in proof.siblings:
        expected_side = "left" if position % 2 else "right"
        if side != expected_side:
            return False
        if side == "left":
            current = _parent(sibling, current)
        else:
            if position + 1 >= width and sibling != current:
                return False
            current = _parent(current, sibling)
        position //= 2
        width = (width + 1) // 2
    return width == 1 and current == expected_root
