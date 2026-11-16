"""CPU conversion primitives for reviewed Q2_2 projection reconstruction.

GGUF dequantization comes from the exact serving llama.cpp revision. Native
MXFP4 rows use adjacent nibbles and UE8M0 scales; block FP8 uses 32x32 scales.
Requantization changes values and requires separate model-quality validation.
"""
import numpy as np
import ml_dtypes
from gguf import GGMLQuantizationType
from gguf.quants import dequantize

FP4 = np.array([0, .5, 1, 1.5, 2, 3, 4, 6,
                -0., -.5, -1, -1.5, -2, -3, -4, -6], dtype=np.float32)
MIDPOINTS = (FP4[:7] + FP4[1:8]) / 2
SUPPORTED_SOURCE_TYPES = frozenset({8, 14, 18, 21, 39})


def matrix(values):
    values = np.asarray(values, dtype=np.float32)
    if values.ndim != 2 or not values.size or not np.isfinite(values).all():
        raise ValueError('Expected a nonempty finite matrix')
    rows, cols = values.shape
    if rows % 32 or cols % 32:
        raise ValueError('Native projection blocks require dimensions divisible by 32')
    return values


def decode_gguf(raw, kind, rows, cols):
    if kind not in SUPPORTED_SOURCE_TYPES:
        raise ValueError('Unreviewed GGUF quantization type')
    values = dequantize(np.frombuffer(raw, dtype=np.uint8), GGMLQuantizationType(kind))
    if values.size != rows * cols:
        raise ValueError('GGUF slice does not match its declared logical shape')
    return matrix(values.reshape(rows, cols))


def power_of_two_scales(maximum, qmax):
    maximum = np.asarray(maximum, dtype=np.float32)
    if not np.isfinite(maximum).all() or np.any(maximum < 0):
        raise ValueError('Invalid quantization maximum')
    exponent = np.zeros(maximum.shape, dtype=np.int32)
    np.ceil(np.log2(np.divide(maximum, qmax, out=np.ones_like(maximum), where=maximum > 0)), out=exponent, casting='unsafe')
    exponent = np.where(maximum > 0, exponent, 0)
    if np.any(exponent < -127) or np.any(exponent > 127):
        raise ValueError('Quantization scale outside finite UE8M0 range')
    return (exponent + 127).astype(np.uint8), np.exp2(exponent.astype(np.float32))


def encode_mxfp4(values):
    values = matrix(values)
    rows, cols = values.shape
    blocks = values.reshape(rows, cols // 32, 32)
    scales, scale_float = power_of_two_scales(np.abs(blocks).max(axis=-1), 6.)
    normalized = blocks / scale_float[..., None]
    absolute = np.abs(normalized)
    codes = np.searchsorted(MIDPOINTS, absolute, side='left').astype(np.uint8)
    # Nearest-even at exact midpoints, rather than a systematic lower tie bias.
    safe_index = np.minimum(codes, 6)
    ties = (codes < 7) & (absolute == MIDPOINTS[safe_index]) & ((codes & 1) != 0)
    codes += ties.astype(np.uint8)
    codes |= np.signbit(normalized).astype(np.uint8) << 3
    codes = codes.reshape(rows, cols)
    packed = (codes[:, 0::2] | (codes[:, 1::2] << 4)).astype(np.uint8)
    return packed, scales


def decode_mxfp4(packed, scales):
    packed = np.asarray(packed, dtype=np.uint8)
    scales = np.asarray(scales, dtype=np.uint8)
    if packed.ndim != 2 or scales.shape != (packed.shape[0], packed.shape[1] // 16) or np.any(scales == 255):
        raise ValueError('Invalid native MXFP4 layout or scale')
    codes = np.stack([packed & 15, packed >> 4], axis=-1).reshape(packed.shape[0], -1)
    return FP4[codes] * np.repeat(np.exp2(scales.astype(np.float32) - 127), 32, axis=1)


def repack_mxfp4_losslessly(raw, rows, cols):
    if rows % 32 or cols % 32:
        raise ValueError('Unsupported MXFP4 slice shape')
    data = np.frombuffer(raw, dtype=np.uint8)
    if data.size != rows * (cols // 32) * 17:
        raise ValueError('Wrong GGUF MXFP4 slice length')
    blocks = data.reshape(rows, cols // 32, 17)
    scales = blocks[..., 0].copy()
    if np.any(scales == 255):
        raise ValueError('Nonfinite MXFP4 scale')
    qs = blocks[..., 1:]
    codes = np.concatenate([qs & 15, qs >> 4], axis=-1).reshape(rows, cols)
    return (codes[:, 0::2] | (codes[:, 1::2] << 4)).copy(), scales


def encode_block_fp8(values):
    values = matrix(values)
    rows, cols = values.shape
    blocks = values.reshape(rows // 32, 32, cols // 32, 32).transpose(0, 2, 1, 3)
    scales, scale_float = power_of_two_scales(np.abs(blocks).max(axis=(-2, -1)), 448.)
    quant = (blocks / scale_float[..., None, None]).astype(ml_dtypes.float8_e4m3fn)
    encoded = quant.transpose(0, 2, 1, 3).reshape(rows, cols).copy().view(np.uint8)
    return encoded, scales


def decode_block_fp8(encoded, scales):
    encoded = np.asarray(encoded, dtype=np.uint8)
    scales = np.asarray(scales, dtype=np.uint8)
    rows, cols = encoded.shape
    if rows % 32 or cols % 32 or scales.shape != (rows // 32, cols // 32) or np.any(scales == 255):
        raise ValueError('Invalid native block FP8 layout or scale')
    blocks = encoded.view(ml_dtypes.float8_e4m3fn).astype(np.float32).reshape(rows // 32, 32, cols // 32, 32)
    return (blocks * np.exp2(scales.astype(np.float32) - 127)[:, None, :, None]).reshape(rows, cols)


def error_metrics(expected, actual):
    expected = np.asarray(expected, dtype=np.float64)
    actual = np.asarray(actual, dtype=np.float64)
    if expected.shape != actual.shape or not np.isfinite(expected).all() or not np.isfinite(actual).all():
        raise ValueError('Invalid reconstruction values')
    error = actual - expected
    rms = float(np.sqrt(np.mean(expected * expected)))
    error_rms = float(np.sqrt(np.mean(error * error)))
    return {'relative_rms_error': error_rms / rms if rms else error_rms,
            'maximum_absolute_error': float(np.max(np.abs(error))),
            'source_rms': rms}
