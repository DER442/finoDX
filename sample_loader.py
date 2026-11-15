"""Load WAV samples into memory (float32 stereo). WAV FILES ONLY - nothing else is ever decoded or played."""
from __future__ import annotations

import os
import struct
from typing import Dict, Optional, Tuple

import numpy as np

MAX_BYTES = 120 * 1024 * 1024


class WavError(ValueError):
    pass


def _chunks(raw: bytes):
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise WavError("not a WAV file (no RIFF/WAVE header)")
    pos, n = 12, len(raw)
    fmt = data = None
    while pos + 8 <= n:
        cid = raw[pos:pos + 4]
        size = struct.unpack_from("<I", raw, pos + 4)[0]
        body = pos + 8
        if cid == b"fmt " and fmt is None:
            fmt = raw[body:body + size]
        elif cid == b"data" and data is None:
            end = body + size
            if size == 0xFFFFFFFF or end > n:      # streamed / truncated file: take what is there
                end = n
            data = raw[body:end]
        if fmt is not None and data is not None:
            break
        pos = body + size + (size & 1)             # chunks are padded to an even size
    if fmt is None or len(fmt) < 16:
        raise WavError("WAV has no fmt chunk")
    if data is None:
        raise WavError("WAV has no audio data")
    return fmt, data


def trim_lead(a: np.ndarray, threshold: float = 0.002, keep: int = 8) -> np.ndarray:
    """Cut the silence before the hit, so every instrument sounds on the beat (no per-sample lead-in delay)."""
    loud = np.flatnonzero(np.max(np.abs(a), axis=1) > threshold)
    if len(loud) == 0:
        return a
    start = max(0, int(loud[0]) - keep)
    return np.ascontiguousarray(a[start:]) if start else a


def decode_wav(raw: bytes, target_rate: int = 44100) -> np.ndarray:
    """PCM 8/16/24/32-bit, float 32/64, WAVE_FORMAT_EXTENSIBLE, any channel count -> float32 (n, 2)."""
    fmt, data = _chunks(raw)
    tag, ch, rate, _br, _blk, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = struct.unpack_from("<H", fmt, 24)[0]
    if ch < 1 or rate < 1:
        raise WavError("bad WAV header")
    if tag == 1 and bits in (8, 16, 24, 32):
        pass
    elif tag == 3 and bits in (32, 64):
        pass
    else:
        raise WavError(f"unsupported WAV encoding (format {tag}, {bits}-bit): use PCM or float WAV")
    frame = ch * bits // 8
    data = data[: len(data) // frame * frame]
    if not data:
        raise WavError("WAV has no audio data")
    if tag == 3:
        a = np.frombuffer(data, dtype="<f4" if bits == 32 else "<f8").astype(np.float32)
    elif bits == 8:
        a = (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif bits == 16:
        a = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    elif bits == 24:
        b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 0x800000, v - 0x1000000, v)
        a = v.astype(np.float32) / 8388608.0
    else:
        a = np.frombuffer(data, dtype="<i4").astype(np.float32) / 2147483648.0
    a = np.nan_to_num(a).reshape(-1, ch)
    a = np.column_stack([a[:, 0], a[:, 0]]) if ch == 1 else a[:, :2]
    if rate != target_rate and len(a) > 1:
        new_len = max(1, int(round(len(a) * target_rate / float(rate))))
        pos = np.linspace(0, len(a) - 1, new_len)
        i0 = np.floor(pos).astype(np.int64)
        i1 = np.minimum(i0 + 1, len(a) - 1)
        fr = (pos - i0).astype(np.float32)[:, None]
        a = a[i0] * (1.0 - fr) + a[i1] * fr
    a = np.clip(a, -1.0, 1.0).astype(np.float32)
    a = trim_lead(a)
    k = min(len(a), 48)                              # 1 ms fade-out: no click if a sample stops abruptly
    if k > 1:
        a[-k:] *= np.linspace(1.0, 0.0, k, dtype=np.float32)[:, None]
    return np.ascontiguousarray(a)


def load_wav(path: str, target_rate: int = 44100) -> np.ndarray:
    if not path.lower().endswith(".wav"):
        raise WavError("only .wav samples are played")
    if os.path.getsize(path) > MAX_BYTES:
        raise WavError("file too large")
    with open(path, "rb") as f:
        return decode_wav(f.read(), target_rate)


class SampleBank:
    """Caches decoded samples keyed by absolute path."""

    def __init__(self, rate: int = 44100):
        self.rate = rate
        self._cache: Dict[str, np.ndarray] = {}
        self.errors: Dict[str, str] = {}

    def get(self, path: str) -> Optional[np.ndarray]:
        path = os.path.abspath(path)
        if path in self._cache:
            return self._cache[path]
        if path in self.errors:
            return None
        try:
            data = load_wav(path, self.rate)
            self._cache[path] = data
            return data
        except Exception as e:
            self.errors[path] = str(e)
            return None

    def preload(self, paths) -> Tuple[int, int]:
        ok = fail = 0
        for p in paths:
            if self.get(p) is not None:
                ok += 1
            else:
                fail += 1
        return ok, fail

    def prune(self, keep):
        keep = {os.path.abspath(p) for p in keep}
        for p in [p for p in self._cache if p not in keep]:
            del self._cache[p]
        for p in [p for p in self.errors if p not in keep]:
            del self.errors[p]

    def clear(self):
        self._cache.clear()
        self.errors.clear()
