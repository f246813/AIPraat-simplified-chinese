"""Small adapter of PanPhon's FeatureTable; see NOTICE.md and LICENSE."""
from __future__ import annotations

import csv
import unicodedata
from pathlib import Path


class FeatureTable:
    TRIE_LEAF_MARKER = None

    def __init__(self):
        # Adapted _read_bases: stdlib CSV replaces pandas; raw +/-/0 are sufficient.
        with Path(__file__).with_name("ipa_all.csv").open(encoding="utf-8", newline="") as handle:
            self.seg_dict = {
                self.normalize(row.pop("ipa")): row
                for row in csv.DictReader(handle)
            }
        self.seg_trie = self._build_seg_trie()

    @staticmethod
    def normalize(data: str) -> str:
        return unicodedata.normalize('NFD', data)

    def _build_seg_trie(self) -> dict:
        trie: dict = {}
        for seg in self.seg_dict.keys():
            node = trie
            for char in seg:
                if char not in node:
                    node[char] = {}
                node = node[char]
            node[self.TRIE_LEAF_MARKER] = None
        return trie

    def longest_one_seg_prefix(self, word: str, normalize: bool = True) -> str:
        """Return longest Unicode IPA prefix of a word

        Args:
            word (str): input word as Unicode IPA string
            normalize (bool): whether the word should be pre-normalized

        Returns:
            str: longest single-segment prefix of `word` in database
        """
        if normalize:
            word = FeatureTable.normalize(word)
        last_found_length = 0
        node = self.seg_trie
        for pos in range(len(word) + 1):
            if pos == len(word) or word[pos] not in node:
                return word[:last_found_length]
            node = node[word[pos]]
            if self.TRIE_LEAF_MARKER in node:
                last_found_length = pos + 1
        return ''

    def _segs(self, word: str, *, include_valid: bool=True, include_invalid: bool, normalize: bool=True) -> list[str]:
        if normalize:
            word = FeatureTable.normalize(word)
        segs = []
        while word:
            m = self.longest_one_seg_prefix(word, False)
            if m:
                if include_valid:
                    segs.append(m)
                word = word[len(m):]
            else:
                if include_invalid:
                    segs.append(word[0])
                word = word[1:]
        return segs

    def segments(self, ipa: str) -> list[dict[str, str]] | None:
        # Unlike ipa_segs(), do not silently drop unknown symbols.
        normalized = self.normalize(ipa)
        segments = self._segs(normalized, include_invalid=True, normalize=False)
        if not segments or any(segment not in self.seg_dict for segment in segments):
            return None
        return [self.seg_dict[segment] for segment in segments]
