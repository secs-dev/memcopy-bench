#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <immintrin.h>

namespace memcopy_bench::scenarios {

namespace detail {

// Method 1: Branchless unrolled unaligned AVX2 for 128..256 B.
__attribute__((target("avx2"))) inline void
copy_avx2_small_128_256(std::uint8_t *d, const std::uint8_t *s,
                        std::size_t len) {
  if (len == 128) {
    __m256i v0 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    __m256i v1 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 32));
    __m256i v2 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 64));
    __m256i v3 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 96));
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d), v0);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 32), v1);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 64), v2);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 96), v3);
    return;
  }
  if (len == 256) {
    __m256i v0 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    __m256i v1 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 32));
    __m256i v2 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 64));
    __m256i v3 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 96));
    __m256i v4 =
        _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 128));
    __m256i v5 =
        _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 160));
    __m256i v6 =
        _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 192));
    __m256i v7 =
        _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 224));
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d), v0);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 32), v1);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 64), v2);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 96), v3);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 128), v4);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 160), v5);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 192), v6);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 224), v7);
    return;
  }

  const std::uint8_t *s_end = s + len;
  std::uint8_t *d_end = d + len;
  while (len > 32) {
    __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d), v);
    d += 32;
    s += 32;
    len -= 32;
  }
  if (len > 0) {
    __m256i tail =
        _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s_end - 32));
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d_end - 32), tail);
  }
}

// Method 2: 32B-aligned 8x-unrolled AVX2 temporal copy for L1/L2 (32K..64K).
__attribute__((target("avx2"))) inline void
copy_avx2_aligned_temporal8x(std::uint8_t *d, const std::uint8_t *s,
                             std::size_t len) {
  __m256i head = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
  const std::size_t align_off =
      32 - (reinterpret_cast<std::uintptr_t>(d) & 31);
  const std::uint8_t *s_end = s + len;
  std::uint8_t *d_end = d + len;

  _mm256_storeu_si256(reinterpret_cast<__m256i *>(d), head);
  d += align_off;
  s += align_off;
  len -= align_off;

  while (len >= 256) {
    __m256i v0 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    __m256i v1 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 32));
    __m256i v2 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 64));
    __m256i v3 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 96));
    __m256i v4 =
        _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 128));
    __m256i v5 =
        _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 160));
    __m256i v6 =
        _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 192));
    __m256i v7 =
        _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 224));
    _mm256_store_si256(reinterpret_cast<__m256i *>(d), v0);
    _mm256_store_si256(reinterpret_cast<__m256i *>(d + 32), v1);
    _mm256_store_si256(reinterpret_cast<__m256i *>(d + 64), v2);
    _mm256_store_si256(reinterpret_cast<__m256i *>(d + 96), v3);
    _mm256_store_si256(reinterpret_cast<__m256i *>(d + 128), v4);
    _mm256_store_si256(reinterpret_cast<__m256i *>(d + 160), v5);
    _mm256_store_si256(reinterpret_cast<__m256i *>(d + 192), v6);
    _mm256_store_si256(reinterpret_cast<__m256i *>(d + 224), v7);
    d += 256;
    s += 256;
    len -= 256;
  }

  while (len > 32) {
    __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    _mm256_store_si256(reinterpret_cast<__m256i *>(d), v);
    d += 32;
    s += 32;
    len -= 32;
  }
  if (len > 0) {
    __m256i tail =
        _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s_end - 32));
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d_end - 32), tail);
  }
}

} // namespace detail

struct CandidateScenario {
  static inline void copy(void *dst, const void *src, std::size_t len) {
    if (len == 0) {
      return;
    }
    if (len >= 128 && len <= 256) {
      detail::copy_avx2_small_128_256(static_cast<std::uint8_t *>(dst),
                                      static_cast<const std::uint8_t *>(src),
                                      len);
      return;
    }
    if (len >= 32768 && len <= 65536) {
      detail::copy_avx2_aligned_temporal8x(
          static_cast<std::uint8_t *>(dst),
          static_cast<const std::uint8_t *>(src), len);
      return;
    }
    std::memcpy(dst, src, len);
  }
};

} // namespace memcopy_bench::scenarios
