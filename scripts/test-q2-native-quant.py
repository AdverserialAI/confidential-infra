#!/usr/bin/env python3
"""Numeric encoding/ordering tests against pinned GGUF decoders, on CPU."""
import importlib.util
from pathlib import Path
import unittest
import numpy as np
from gguf import GGMLQuantizationType, GGML_QUANT_SIZES

spec = importlib.util.spec_from_file_location('quant', Path(__file__).with_name('q2-native-quant.py'))
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


class QuantizationTests(unittest.TestCase):
    def test_native_mxfp4_bit_preserving_inverse_of_pinned_gguf_layout(self):
        rng = np.random.default_rng(8)
        rows, cols = 64, 256
        data = rng.integers(0, 256, (rows, cols // 32, 17), dtype=np.uint8)
        data[..., 0] = rng.integers(115, 130, data[..., 0].shape, dtype=np.uint8)
        expected = q.decode_gguf(data.tobytes(), 39, rows, cols)
        packed, scales = q.repack_mxfp4_losslessly(data.tobytes(), rows, cols)
        np.testing.assert_array_equal(q.decode_mxfp4(packed, scales), expected)
        codes = np.stack([packed & 15, packed >> 4], axis=-1).reshape(rows, cols // 32, 32)
        reconstructed = np.concatenate([scales[..., None], (codes[..., :16] | (codes[..., 16:] << 4))], axis=-1)
        np.testing.assert_array_equal(reconstructed, data)

    def test_all_selected_iq_q8_q6_decoders_to_native_fp4_and_fp8(self):
        rng = np.random.default_rng(3)
        rows, cols = 64, 256
        for kind in (8, 14, 18, 21):
            block, size = GGML_QUANT_SIZES[GGMLQuantizationType(kind)]
            raw = rng.integers(0, 256, (rows * cols // block, size), dtype=np.uint8)
            scale = np.frombuffer(np.float16(.003).tobytes(), dtype=np.uint8)
            if kind == 14: raw[:, -2:] = scale
            else: raw[:, :2] = scale
            values = q.decode_gguf(raw.tobytes(), kind, rows, cols)
            with self.subTest(kind=kind):
                fp4, scales = q.encode_mxfp4(values)
                self.assertLess(q.error_metrics(values, q.decode_mxfp4(fp4, scales))['relative_rms_error'], .18)
                fp8, scales = q.encode_block_fp8(values)
                self.assertLess(q.error_metrics(values, q.decode_block_fp8(fp8, scales))['relative_rms_error'], .05)

    def test_zero_blocks_and_exact_even_ties(self):
        data = np.zeros((32, 32), dtype=np.float32)
        data[0, :5] = [.25, .75, 1.25, 1.75, 6.]
        packed, scales = q.encode_mxfp4(data)
        self.assertEqual(scales[0, 0], 127)
        np.testing.assert_array_equal(q.decode_mxfp4(packed, scales)[0, :5], [0, 1, 1, 2, 6])
        np.testing.assert_array_equal(q.decode_mxfp4(*q.encode_mxfp4(np.zeros_like(data))), np.zeros_like(data))

    def test_invalid_shapes_nan_and_unreviewed_source_are_rejected(self):
        for a in [np.zeros((2, 32)), np.full((32, 32), np.nan), np.zeros((32, 31))]:
            with self.subTest(), self.assertRaises(ValueError):q.encode_mxfp4(a)
        with self.assertRaises(ValueError):q.decode_gguf(b'', 17, 32, 256)
        with self.assertRaises(ValueError):q.repack_mxfp4_losslessly(b'', 32, 256)


if __name__ == '__main__':
    unittest.main()
