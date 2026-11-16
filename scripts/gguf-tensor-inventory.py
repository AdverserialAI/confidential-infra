#!/usr/bin/env python3
"""Inventory immutable public GGUF headers with bounded, checked HTTP ranges.

Only bounded file prefixes are fetched (the last prefix may extend slightly
past the header); no complete weight file, conversion, model execution,
credential lookup or cloud mutation. Inventory is not proof of vLLM support.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct
import urllib.parse
import urllib.request


class InventoryError(ValueError):
    pass


class HeaderReader:
    FORMATS = {0: 'B', 1: 'b', 2: 'H', 3: 'h', 4: 'I', 5: 'i',
               6: 'f', 7: '?', 10: 'Q', 11: 'q', 12: 'd'}

    def __init__(self, read_range, *, limit=16 * 1024 * 1024, chunk=262144):
        self.read_range = read_range
        self.limit = limit
        self.chunk = chunk
        self.buffer = bytearray()
        self.offset = 0

    def take(self, size):
        end = self.offset + size
        if size < 0 or end > self.limit:
            raise InventoryError('GGUF metadata exceeds the explicit byte budget')
        while len(self.buffer) < end:
            start = len(self.buffer)
            count = min(max(self.chunk, end - start), self.limit - start)
            part = self.read_range(start, count)
            if not part or len(part) > count:
                raise InventoryError('Short metadata range')
            self.buffer.extend(part)
            if len(part) < count and len(self.buffer) < end:
                raise InventoryError('Truncated metadata range')
        part = self.buffer[self.offset:end]
        self.offset = end
        return bytes(part)

    def scalar(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]

    def string(self, keep=True):
        size = self.scalar('Q')
        raw = self.take(size)
        return raw.decode('utf-8') if keep else None

    def value(self, kind, *, keep=False, depth=0):
        if kind in self.FORMATS:
            result = self.scalar(self.FORMATS[kind])
            return result if keep else None
        if kind == 8:
            return self.string(keep)
        if kind == 9:
            item_kind, count = self.scalar('I'), self.scalar('Q')
            if depth >= 2 or count > 2_000_000:
                raise InventoryError('Unsupported metadata array budget')
            if keep and count > 4096:
                start = self.offset
                if item_kind in self.FORMATS:
                    self.take(count * struct.calcsize('<' + self.FORMATS[item_kind]))
                else:
                    for _ in range(count):
                        self.value(item_kind, keep=False, depth=depth + 1)
                return {'array_item_type': item_kind, 'array_count': count,
                        'encoded_values_sha256': hashlib.sha256(self.buffer[start:self.offset]).hexdigest()}
            if not keep and item_kind in self.FORMATS:
                self.take(count * struct.calcsize('<' + self.FORMATS[item_kind]))
                return None
            if keep:
                return [self.value(item_kind, keep=True, depth=depth + 1) for _ in range(count)]
            for _ in range(count):
                self.value(item_kind, keep=False, depth=depth + 1)
            return None
        raise InventoryError('Unknown GGUF metadata type')

    def inventory(self):
        if self.take(4) != b'GGUF':
            raise InventoryError('Not a little-endian GGUF file')
        version = self.scalar('I')
        if version not in (2, 3):
            raise InventoryError('Unsupported GGUF version')
        n_tensors, n_metadata = self.scalar('Q'), self.scalar('Q')
        if n_tensors > 2_000_000 or n_metadata > 2_000_000:
            raise InventoryError('Implausible GGUF entry count')
        metadata = {}
        for _ in range(n_metadata):
            name = self.string()
            keep = name in ('general.architecture', 'general.file_type', 'general.alignment') or name.startswith(('split.', 'deepseek41.'))
            value = self.value(self.scalar('I'), keep=keep)
            if keep:
                if name in metadata:
                    raise InventoryError('Duplicate selected metadata key')
                metadata[name] = value
        tensors, seen = [], set()
        for _ in range(n_tensors):
            name, dimensions = self.string(), self.scalar('I')
            if not 1 <= dimensions <= 8 or name in seen:
                raise InventoryError('Invalid tensor dimensions or duplicate name')
            shape = [self.scalar('Q') for _ in range(dimensions)]
            if any(d == 0 for d in shape):
                raise InventoryError('Empty tensor dimension')
            kind, offset = self.scalar('I'), self.scalar('Q')
            seen.add(name)
            tensors.append({'name': name, 'gguf_shape': shape,
                            'logical_numpy_shape': list(reversed(shape)),
                            'ggml_type_id': kind, 'data_offset': offset})
        alignment = metadata.get('general.alignment', 32)
        if not isinstance(alignment, int) or alignment < 1 or alignment > 65536 or alignment & (alignment - 1):
            raise InventoryError('Invalid tensor-data alignment')
        return {'gguf_version': version, 'metadata': metadata,
                'tensor_count': n_tensors, 'header_bytes_parsed': self.offset,
                'metadata_range_bytes_fetched': len(self.buffer),
                'parsed_header_sha256': hashlib.sha256(self.buffer[:self.offset]).hexdigest(),
                'data_start_offset': (self.offset + alignment - 1) // alignment * alignment,
                'ggml_type_counts': dict(Counter(str(t['ggml_type_id']) for t in tensors)),
                'tensors': tensors,
                'scope': 'Header metadata only; payload hashes and numerical equivalence not verified'}


def public_hf_reader(repo, revision, filename):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise InventoryError('A public HF model and immutable revision are required')
    if not filename.endswith('.gguf') or any(p in ('', '.', '..') for p in filename.split('/')):
        raise InventoryError('Invalid GGUF filename')
    base = 'https://huggingface.co/' + repo + '/resolve/' + revision + '/' + urllib.parse.quote(filename, safe='/')

    def read_range(start, size):
        end = start + size - 1
        # Separate resolve URL per range avoids stale CDN range-cache entries.
        request = urllib.request.Request(base + '?metadata_range=' + str(start), headers={
            'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity',
            'User-Agent': 'Adverserial-GGUF-metadata-review/1.0'})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                if response.status != 206 or not match:
                    raise InventoryError('Server did not honor the exact metadata range')
                first, last, total = map(int, match.groups())
                if first != start or last != min(end, total - 1) or last < first:
                    raise InventoryError('Server returned a mismatched metadata range')
                result = response.read(size + 1)
                if len(result) != last - first + 1:
                    raise InventoryError('Unexpected metadata response length')
                return result
        except Exception:
            raise InventoryError('Public metadata range failed; provider URL/details withheld') from None
    return read_range


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--file', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = HeaderReader(public_hf_reader(args.repo, args.revision, args.file)).inventory()
        result['source'] = {'repo': args.repo, 'revision': args.revision, 'file': args.file}
        with args.output.open('x') as output:
            json.dump(result, output, indent=2)
            output.write('\n')
        print(json.dumps({k: result[k] for k in ['source', 'tensor_count', 'header_bytes_parsed',
                                                 'metadata_range_bytes_fetched', 'ggml_type_counts', 'scope']}))
    except Exception as error:
        print(json.dumps({'inventory_failed': True, 'error_class': type(error).__name__}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
