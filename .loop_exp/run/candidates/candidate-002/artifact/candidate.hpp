#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <immintrin.h>

namespace memcopy_bench::scenarios {

namespace detail {

__attribute__((target("avx2"))) inline void
copy_avx2_aligned_temporal(void *dst, const void *src, std::size_t len) {
  auto *d = static_cast<std::uint8_t *>(dst);
  const auto *s = static_cast<const std::uint8_t *>(src);

  __m256i head = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
  const std::size_t align_off =
      32 - (reinterpret_cast<std::uintptr_t>(d) & 31);
  const std::uint8_t *s_end = s + len;
  std::uint8_t *d_end = d + len;

  _mm256_storeu_si256(reinterpret_cast<__m256i *>(d), head);
  d += align_off;
  s += align_off;
  len -= align_off;

  while (len >= 128) {
    __m256i v0 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    __m256i v1 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 32));
    __m256i v2 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 64));
    __m256i v3 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 96));
    _mm256_store_si256(reinterpret_cast<__m256i *>(d), v0);
    _mm256_store_si256(reinterpret_cast<__m256i *>(d + 32), v1);
    _mm256_store_si256(reinterpret_cast<__m256i *>(d + 64), v2);
    _mm256_store_si256(reinterpret_cast<__m256i *>(d + 96), v3);
    d += 128;
    s += 128;
    len -= 128;
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

__attribute__((target("avx2"))) inline void
copy_avx2_aligned_stream(void *dst, const void *src, std::size_t len) {
  auto *d = static_cast<std::uint8_t *>(dst);
  const auto *s = static_cast<const std::uint8_t *>(src);

  __m256i head = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
  const std::size_t align_off =
      32 - (reinterpret_cast<std::uintptr_t>(d) & 31);
  const std::uint8_t *s_end = s + len;
  std::uint8_t *d_end = d + len;

  _mm256_storeu_si256(reinterpret_cast<__m256i *>(d), head);
  d += align_off;
  s += align_off;
  len -= align_off;

  constexpr std::size_t kPrefetchDist = 512;
  while (len >= 128) {
    _mm_prefetch(reinterpret_cast<const char *>(s + kPrefetchDist),
                 _MM_HINT_NTA);
    _mm_prefetch(reinterpret_cast<const char *>(s + kPrefetchDist + 64),
                 _MM_HINT_NTA);
    __m256i v0 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    __m256i v1 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 32));
    __m256i v2 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 64));
    __m256i v3 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 96));
    _mm256_stream_si256(reinterpret_cast<__m256i *>(d), v0);
    _mm256_stream_si256(reinterpret_cast<__m256i *>(d + 32), v1);
    _mm256_stream_si256(reinterpret_cast<__m256i *>(d + 64), v2);
    _mm256_stream_si256(reinterpret_cast<__m256i *>(d + 96), v3);
    d += 128;
    s += 128;
    len -= 128;
  }

  while (len > 32) {
    __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    _mm256_stream_si256(reinterpret_cast<__m256i *>(d), v);
    d += 32;
    s += 32;
    len -= 32;
  }
  if (len > 0) {
    __m256i tail =
        _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s_end - 32));
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d_end - 32), tail);
  }
  _mm_sfence();
}

} // namespace detail

struct CandidateScenario {
  static void copy(void *dst, const void *src, std::size_t len) {
    if (len == 0) {
      return;
    }
    if (len < 16384 || (len >= 131072 && len < 524288)) {
      std::memcpy(dst, src, len);
      return;
    }
    if (len < 8388608) {
      detail::copy_avx2_aligned_temporal(dst, src, len);
      return;
    }
    detail::copy_avx2_aligned_stream(dst, src, len);
  }
};

} // namespace memcopy_bench::scenarios
