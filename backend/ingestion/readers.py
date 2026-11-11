"""Streaming dataset readers: one raw ``dict`` per input row, in file order.

Rows are yielded as they are parsed, so a multi-gigabyte capture never has to be
held in memory as a parsed document:

* **CSV / TSV** - the delimiter (``,`` tab ``;`` ``|``) is taken from the header line;
* **JSON** - a top-level array, an object wrapping a ``transactions`` (or
  ``records`` / ``data`` / ...) list, or JSON Lines (one object per line), decoded
  incrementally with ``JSONDecoder.raw_decode``;
* **XML** - ``iterparse`` with elements released as soon as they are converted.
  Any element that carries a transaction id is a record, whatever its tag or depth;
  list fields may be nested elements, repeated tags or attribute-only elements.

Every format may be gzip-compressed (``.csv.gz``, ``.json.gz``, ``.xml.gz``).
"""

from __future__ import annotations

import csv
import gzip
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import IO, Any, Iterator

from backend.ingestion.schema import ALIASES, clean_key

SUFFIX_FORMATS = {".csv": "csv", ".tsv": "csv", ".json": "json", ".jsonl": "json", ".ndjson": "json", ".xml": "xml"}
SUPPORTED_SUFFIXES = tuple(SUFFIX_FORMATS) + tuple(f"{suffix}.gz" for suffix in SUFFIX_FORMATS)
WRAPPER_KEYS = ("transactions", "records", "observations", "data", "rows", "items", "results", "result", "txs", "events")
_TXID_KEYS = frozenset(ALIASES["txid"])
_WHITESPACE = re.compile(r"[ \t\r\n]*")
_DELIMITERS = (",", "\t", ";", "|")


def dataset_suffix(path: str | Path) -> tuple[str, bool]:
    """(format suffix, is gzip) for ``transactions.csv`` / ``capture.json.gz`` ..."""
    suffixes = [suffix.lower() for suffix in Path(path).suffixes]
    gzipped = bool(suffixes) and suffixes[-1] == ".gz"
    if gzipped:
        suffixes = suffixes[:-1]
    return (suffixes[-1] if suffixes else ""), gzipped


def detect_format(path: str | Path) -> str:
    """Return ``csv`` / ``json`` / ``xml`` from the file extension."""
    suffix, _ = dataset_suffix(path)
    if suffix not in SUFFIX_FORMATS:
        raise ValueError(f"Unsupported dataset format for path: {path}")
    return SUFFIX_FORMATS[suffix]


def is_supported(path: str | Path) -> bool:
    return dataset_suffix(path)[0] in SUFFIX_FORMATS


def _open_text(path: Path) -> IO[str]:
    if dataset_suffix(path)[1]:
        return gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="")
    return path.open("r", encoding="utf-8-sig", errors="replace", newline="")


def _open_binary(path: Path) -> IO[bytes]:
    return gzip.open(path, "rb") if dataset_suffix(path)[1] else path.open("rb")


# ── CSV ─────────────────────────────────────────────────────────────────────
def _sniff_delimiter(header: str) -> str:
    counts = dict.fromkeys(_DELIMITERS, 0)
    quoted = False
    for char in header:
        if char == '"':
            quoted = not quoted
        elif not quoted and char in counts:
            counts[char] += 1
    best = max(_DELIMITERS, key=lambda delimiter: counts[delimiter])
    return best if counts[best] else ","


def read_csv(path: str | Path) -> Iterator[dict[str, Any]]:
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file does not exist: {csv_path}")
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))  # very wide transactions (thousands of inputs) exceed the default 128 KiB
    with _open_text(csv_path) as handle:
        header = handle.readline()
        if not header.strip():
            return
        delimiter = _sniff_delimiter(header)
        columns = [column.strip() for column in next(csv.reader([header], delimiter=delimiter))]
        for values in csv.reader(handle, delimiter=delimiter):
            if not values or (len(values) == 1 and not values[0].strip()):
                continue
            yield dict(zip(columns, values))


# ── JSON ────────────────────────────────────────────────────────────────────
class _JsonStream:
    """Incremental JSON value reader over a text stream."""

    CHUNK = 1 << 20

    def __init__(self, handle: IO[str]) -> None:
        self.handle = handle
        self.buffer = ""
        self.position = 0
        self.finished = False
        self.decoder = json.JSONDecoder()

    def _fill(self) -> bool:
        if self.finished:
            return False
        chunk = self.handle.read(self.CHUNK)
        if not chunk:
            self.finished = True
            return False
        self.buffer = self.buffer[self.position:] + chunk
        self.position = 0
        return True

    def peek(self) -> str | None:
        """The next non-whitespace character, or None at end of input."""
        while True:
            self.position = _WHITESPACE.match(self.buffer, self.position).end()
            if self.position < len(self.buffer):
                return self.buffer[self.position]
            if not self._fill():
                return None

    def value(self) -> Any:
        while True:
            try:
                decoded, end = self.decoder.raw_decode(self.buffer, self.position)
            except json.JSONDecodeError as exc:
                if not self._fill():
                    raise ValueError(f"Invalid JSON near character {exc.pos}: {exc.msg}") from exc
                continue
            # A bare number that ends exactly at the buffer end may be cut off ("12" of "123").
            if end == len(self.buffer) and not isinstance(decoded, (dict, list, str)) and self._fill():
                continue
            self.position = end
            return decoded

    def array_items(self) -> Iterator[Any]:
        while True:
            char = self.peek()
            if char is None:
                raise ValueError("Invalid JSON: the top-level array is not closed.")
            if char == "]":
                self.position += 1
                return
            if char == ",":
                self.position += 1
                continue
            yield self.value()


def _unwrap(document: Any) -> Iterator[dict[str, Any]]:
    if isinstance(document, list):
        yield from (item for item in document if isinstance(item, dict))
        return
    if not isinstance(document, dict):
        raise ValueError("Unsupported JSON structure. Expected a list or a dict with a transactions list.")
    lowered = {str(key).lower(): key for key in document}
    for name in WRAPPER_KEYS:
        if name in lowered:
            items = document[lowered[name]]
            if not isinstance(items, list):
                raise ValueError(f"JSON '{lowered[name]}' field must be a list.")
            yield from (item for item in items if isinstance(item, dict))
            return
    if any(clean_key(key) in _TXID_KEYS for key in document):  # a single transaction object
        yield document
        return
    for value in document.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            yield from (item for item in value if isinstance(item, dict))
            return
    raise ValueError("JSON object does not contain a 'transactions' list.")


def read_json(path: str | Path) -> Iterator[dict[str, Any]]:
    json_path = Path(path)
    if not json_path.exists():
        raise FileNotFoundError(f"JSON file does not exist: {json_path}")
    with _open_text(json_path) as handle:
        stream = _JsonStream(handle)
        first = stream.peek()
        if first is None:
            return
        if first == "[":
            stream.position += 1
            yield from (item for item in stream.array_items() if isinstance(item, dict))
            return
        if first != "{":
            raise ValueError("Unsupported JSON structure. Expected a list or a dict with a transactions list.")
        document = stream.value()
        if stream.peek() is None:
            yield from _unwrap(document)
            return
        if isinstance(document, dict):  # more values follow: JSON Lines
            yield document
        while stream.peek() is not None:
            item = stream.value()
            if isinstance(item, dict):
                yield item


# ── XML ─────────────────────────────────────────────────────────────────────
def _local(tag: Any) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1] if text.startswith("{") else text


def _xml_value(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        text = (element.text or "").strip()
        return dict(element.attrib) if element.attrib and not text else text
    simple = all(len(child) == 0 and not child.attrib for child in children)
    if simple and len({_local(child.tag) for child in children}) == 1:
        return [text for text in ((child.text or "").strip() for child in children) if text]  # <addresses><address>..</address>..</addresses>
    if simple and not element.attrib:
        return {_local(child.tag): (child.text or "").strip() for child in children}       # <input><address/><amount/></input>
    values = [_xml_value(child) for child in children]
    return values if all(isinstance(value, dict) for value in values) else {**element.attrib, **{_local(child.tag): value for child, value in zip(children, values)}}


def _xml_record(element: ET.Element) -> dict[str, Any] | None:
    names = [_local(child.tag) for child in element]
    if not any(clean_key(name) in _TXID_KEYS for name in names) and not any(clean_key(name) in _TXID_KEYS for name in element.attrib):
        return None
    record: dict[str, Any] = dict(element.attrib)
    repeated: set[str] = set()
    for name, child in zip(names, element):
        value = _xml_value(child)
        if name not in record:
            record[name] = value
        elif name in repeated:
            record[name].append(value)
        else:  # the same tag twice: <input .../><input .../> directly under the transaction
            record[name] = [record[name], value]
            repeated.add(name)
    return record


def read_xml(path: str | Path) -> Iterator[dict[str, Any]]:
    xml_path = Path(path)
    if not xml_path.exists():
        raise FileNotFoundError(f"XML file does not exist: {xml_path}")
    with _open_binary(xml_path) as handle:
        stack: list[ET.Element] = []
        try:
            for event, element in ET.iterparse(handle, events=("start", "end")):
                if event == "start":
                    stack.append(element)
                    continue
                stack.pop()
                if len(element) == 0 and not element.attrib:
                    continue
                record = _xml_record(element)
                if record is None:
                    continue
                yield record
                element.clear()
                if stack:
                    stack[-1].remove(element)  # release the parsed record so memory stays flat
        except ET.ParseError as exc:
            raise ValueError(f"Invalid XML: {exc}") from exc


READERS = {"csv": read_csv, "json": read_json, "xml": read_xml}


def read_rows(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield raw rows from a CSV, JSON or XML dataset (optionally gzip-compressed)."""
    return READERS[detect_format(path)](path)


__all__ = ["SUFFIX_FORMATS", "SUPPORTED_SUFFIXES", "dataset_suffix", "detect_format", "is_supported", "read_csv", "read_json", "read_xml", "read_rows"]
