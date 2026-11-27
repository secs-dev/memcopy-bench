#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <immintrin.h>

namespace memcopy_bench::scenarios {

struct CandidateScenario {
  __attribute__((target("avx2"))) static inline void
  copy(void *dst, const void *src, std::size_t len) {
    if (len == 0) {
      return;
    }
    auto *d = static_cast<std::uint8_t *>(dst);
    const auto *s = static_cast<const std::uint8_t *>(src);

    if (len < 32) {
      if (len >= 16) {
        __m128i lo = _mm_loadu_si128(reinterpret_cast<const __m128i *>(s));
        __m128i hi =
            _mm_loadu_si128(reinterpret_cast<const __m128i *>(s + len - 16));
        _mm_storeu_si128(reinterpret_cast<__m128i *>(d), lo);
        _mm_storeu_si128(reinterpret_cast<__m128i *>(d + len - 16), hi);
        return;
      }
      if (len >= 8) {
        std::uint64_t lo, hi;
        std::memcpy(&lo, s, 8);
        std::memcpy(&hi, s + len - 8, 8);
        std::memcpy(d, &lo, 8);
        std::memcpy(d + len - 8, &hi, 8);
        return;
      }
      if (len >= 4) {
        std::uint32_t lo, hi;
        std::memcpy(&lo, s, 4);
        std::memcpy(&hi, s + len - 4, 4);
        std::memcpy(d, &lo, 4);
        std::memcpy(d + len - 4, &hi, 4);
        return;
      }
      if (len >= 2) {
        std::uint16_t lo, hi;
        std::memcpy(&lo, s, 2);
        std::memcpy(&hi, s + len - 2, 2);
        std::memcpy(d, &lo, 2);
        std::memcpy(d + len - 2, &hi, 2);
        return;
      }
      *d = *s;
      return;
    }

    if (len <= 128) {
      __m256i v0 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
      __m256i v_last =
          _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + len - 32));
      if (len > 64) {
        __m256i v1 =
            _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 32));
        __m256i v2 =
            _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + len - 64));
        _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 32), v1);
        _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + len - 64), v2);
      }
      _mm256_storeu_si256(reinterpret_cast<__m256i *>(d), v0);
      _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + len - 32), v_last);
      return;
    }

    const std::uint8_t *s_end = s + len;
    std::uint8_t *d_end = d + len;

    while (len >= 128) {
      __m256i v0 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
      __m256i v1 =
          _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 32));
      __m256i v2 =
          _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 64));
      __m256i v3 =
          _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 96));
      _mm256_storeu_si256(reinterpret_cast<__m256i *>(d), v0);
      _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 32), v1);
      _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 64), v2);
      _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 96), v3);
      d += 128;
      s += 128;
      len -= 128;
    }

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
};

} // namespace memcopy_bench::scenarios
