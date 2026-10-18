#ifndef MEMCP_BENCH_MEMORY_COPY_COPY_H
#define MEMCP_BENCH_MEMORY_COPY_COPY_H

#include <cstddef>
#include <cstdint>
#include <cstring>

#if defined(__x86_64__) || defined(_M_X64)
#include <immintrin.h>
#endif

namespace memory_bench {

/**
 * Standard library memcpy wrapper baseline.
 */
inline void *copy_memcpy(void *dest, const void *src, size_t len) {
  return std::memcpy(dest, src, len);
}

/**
Copy <len> bytes from src to dest in a loop
*/
inline void *copy_naive(void *dest, const void *src, size_t len) {
  auto *dest_b = static_cast<uint8_t *>(dest);
  const auto *src_b = static_cast<const uint8_t *>(src);
  while (len--) {
    *dest_b++ = *src_b++;
  }
  return dest;
}

/**
A naive copy specialized by the <len>. I.e. we know the size of the copy
at compile-time.
*/
template <size_t Len>
inline constexpr void *copy_naive(void *dest, const void *src) {
  auto *dest_b = static_cast<uint8_t *>(dest);
  const auto *src_b = static_cast<const uint8_t *>(src);
  auto len = Len;
  while (len--) {
    *dest_b++ = *src_b++;
  }
  return dest;
}

/**
 * Fixed-size copy delegating to std::memcpy, which compilers inline
 * into optimal register-width move instructions for compile-time sizes.
 */
template <size_t Len> inline void *copy_fixed(void *dest, const void *src) {
  return std::memcpy(dest, src, Len);
}

/**
 * Duff's device byte-level unrolled copy.
 */
inline void *copy_duff(void *dest, const void *src, size_t len) {
  if (len == 0) {
    return dest;
  }
  auto *d = static_cast<uint8_t *>(dest);
  const auto *s = static_cast<const uint8_t *>(src);
  size_t n = (len + 7) / 8;
  switch (len % 8) {
  case 0:
    do {
      *d++ = *s++;
    case 7:
      *d++ = *s++;
    case 6:
      *d++ = *s++;
    case 5:
      *d++ = *s++;
    case 4:
      *d++ = *s++;
    case 3:
      *d++ = *s++;
    case 2:
      *d++ = *s++;
    case 1:
      *d++ = *s++;
    } while (--n > 0);
  }
  return dest;
}

/**
 * 64-bit scalar word copy with byte-level scalar tail.
 */
inline void *copy_words64(void *dest, const void *src, size_t len) {
  auto *d = static_cast<uint8_t *>(dest);
  const auto *s = static_cast<const uint8_t *>(src);

  while (len >= sizeof(uint64_t)) {
    uint64_t w;
    std::memcpy(&w, s, sizeof(uint64_t));
    std::memcpy(d, &w, sizeof(uint64_t));
    d += sizeof(uint64_t);
    s += sizeof(uint64_t);
    len -= sizeof(uint64_t);
  }
  while (len--) {
    *d++ = *s++;
  }
  return dest;
}

/**
 * 64-bit scalar words unrolled 4x (32 bytes per iteration).
 */
inline void *copy_words64_unrolled4(void *dest, const void *src, size_t len) {
  auto *d = static_cast<uint8_t *>(dest);
  const auto *s = static_cast<const uint8_t *>(src);

  while (len >= 4 * sizeof(uint64_t)) {
    uint64_t w0, w1, w2, w3;
    std::memcpy(&w0, s, sizeof(uint64_t));
    std::memcpy(&w1, s + 8, sizeof(uint64_t));
    std::memcpy(&w2, s + 16, sizeof(uint64_t));
    std::memcpy(&w3, s + 24, sizeof(uint64_t));
    std::memcpy(d, &w0, sizeof(uint64_t));
    std::memcpy(d + 8, &w1, sizeof(uint64_t));
    std::memcpy(d + 16, &w2, sizeof(uint64_t));
    std::memcpy(d + 24, &w3, sizeof(uint64_t));
    d += 32;
    s += 32;
    len -= 32;
  }
  while (len >= sizeof(uint64_t)) {
    uint64_t w;
    std::memcpy(&w, s, sizeof(uint64_t));
    std::memcpy(d, &w, sizeof(uint64_t));
    d += sizeof(uint64_t);
    s += sizeof(uint64_t);
    len -= sizeof(uint64_t);
  }
  while (len--) {
    *d++ = *s++;
  }
  return dest;
}

/**
 * Branchless head-and-tail overlapping copy for small payloads (<= 16 bytes).
 * Employs two overlapping loads/stores to avoid tail branches.
 */
inline void *copy_small_overlapping(void *dest, const void *src, size_t len) {
  auto *d = static_cast<uint8_t *>(dest);
  const auto *s = static_cast<const uint8_t *>(src);
  if (len == 0)
    return dest;
  if (len == 1) {
    *d = *s;
    return dest;
  }
  if (len <= 3) {
    uint16_t head, tail;
    std::memcpy(&head, s, 2);
    std::memcpy(&tail, s + len - 2, 2);
    std::memcpy(d, &head, 2);
    std::memcpy(d + len - 2, &tail, 2);
    return dest;
  }
  if (len <= 7) {
    uint32_t head, tail;
    std::memcpy(&head, s, 4);
    std::memcpy(&tail, s + len - 4, 4);
    std::memcpy(d, &head, 4);
    std::memcpy(d + len - 4, &tail, 4);
    return dest;
  }
  if (len <= 16) {
    uint64_t head, tail;
    std::memcpy(&head, s, 8);
    std::memcpy(&tail, s + len - 8, 8);
    std::memcpy(d, &head, 8);
    std::memcpy(d + len - 8, &tail, 8);
    return dest;
  }
  return copy_words64(dest, src, len);
}

#if defined(__x86_64__) || defined(_M_X64)

/**
 * x86 Enhanced REP MOVSB (ERMS / FSRM).
 * Handled directly by CPU microcode.
 */
inline void *copy_rep_movsb(void *dest, const void *src, size_t len) {
  void *orig_dest = dest;
  __asm__ volatile("rep movsb" : "+D"(dest), "+S"(src), "+c"(len) : : "memory");
  return orig_dest;
}

/**
 * x86 REP MOVSQ for 8-byte quadwords with REP MOVSB tail.
 */
inline void *copy_rep_movsq(void *dest, const void *src, size_t len) {
  void *orig_dest = dest;
  size_t qwords = len / 8;
  size_t bytes = len % 8;
  __asm__ volatile("rep movsq\n\t"
                   "mov %[bytes], %%rcx\n\t"
                   "rep movsb"
                   : "+D"(dest), "+S"(src), "+c"(qwords)
                   : [bytes] "r"(bytes)
                   : "memory");
  return orig_dest;
}

/**
 * SSE2 128-bit unaligned vector copy unrolled 4x (64 bytes/iter).
 */
inline void *copy_sse2(void *dest, const void *src, size_t len) {
  auto *d = static_cast<uint8_t *>(dest);
  const auto *s = static_cast<const uint8_t *>(src);

  while (len >= 64) {
    __m128i v0 = _mm_loadu_si128(reinterpret_cast<const __m128i *>(s));
    __m128i v1 = _mm_loadu_si128(reinterpret_cast<const __m128i *>(s + 16));
    __m128i v2 = _mm_loadu_si128(reinterpret_cast<const __m128i *>(s + 32));
    __m128i v3 = _mm_loadu_si128(reinterpret_cast<const __m128i *>(s + 48));
    _mm_storeu_si128(reinterpret_cast<__m128i *>(d), v0);
    _mm_storeu_si128(reinterpret_cast<__m128i *>(d + 16), v1);
    _mm_storeu_si128(reinterpret_cast<__m128i *>(d + 32), v2);
    _mm_storeu_si128(reinterpret_cast<__m128i *>(d + 48), v3);
    d += 64;
    s += 64;
    len -= 64;
  }
  while (len >= 16) {
    __m128i v = _mm_loadu_si128(reinterpret_cast<const __m128i *>(s));
    _mm_storeu_si128(reinterpret_cast<__m128i *>(d), v);
    d += 16;
    s += 16;
    len -= 16;
  }
  while (len--) {
    *d++ = *s++;
  }
  return dest;
}

/**
 * AVX2 256-bit unaligned vector copy unrolled 4x (128 bytes/iter).
 */
#if defined(__GNUC__) || defined(__clang__)
__attribute__((target("avx2")))
#endif
inline void *copy_avx2(void *dest, const void *src, size_t len) {
  auto *d = static_cast<uint8_t *>(dest);
  const auto *s = static_cast<const uint8_t *>(src);

  while (len >= 128) {
    __m256i v0 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    __m256i v1 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 32));
    __m256i v2 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 64));
    __m256i v3 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 96));
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d), v0);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 32), v1);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 64), v2);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 96), v3);
    d += 128;
    s += 128;
    len -= 128;
  }
  while (len >= 32) {
    __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d), v);
    d += 32;
    s += 32;
    len -= 32;
  }
  while (len--) {
    *d++ = *s++;
  }
  return dest;
}

/**
 * AVX2 Non-temporal streaming stores (VMOVNTDQ).
 * Bypasses CPU caches to avoid Read-For-Ownership (RFO) traffic.
 * Optimal for large buffers exceeding LLC (L3 cache).
 * Aligns destination to 32-byte boundary and issues sfence upon completion.
 */
#if defined(__GNUC__) || defined(__clang__)
__attribute__((target("avx2")))
#endif
inline void *copy_avx2_stream(void *dest, const void *src, size_t len) {
  auto *d = static_cast<uint8_t *>(dest);
  const auto *s = static_cast<const uint8_t *>(src);

  if (len < 128) {
    return copy_naive(dest, src, len);
  }

  // Align destination to 32 bytes (required by _mm256_stream_si256)
  size_t unaligned = (32 - (reinterpret_cast<uintptr_t>(d) & 31)) & 31;
  if (unaligned > 0 && len >= unaligned) {
    for (size_t i = 0; i < unaligned; ++i) {
      *d++ = *s++;
    }
    len -= unaligned;
  }

  while (len >= 128) {
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
  while (len >= 32) {
    __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    _mm256_stream_si256(reinterpret_cast<__m256i *>(d), v);
    d += 32;
    s += 32;
    len -= 32;
  }
  while (len--) {
    *d++ = *s++;
  }
  _mm_sfence();
  return dest;
}

/**
 * AVX2 vector copy with software prefetching (PREFETCHT0).
 */
#if defined(__GNUC__) || defined(__clang__)
__attribute__((target("avx2")))
#endif
inline void *copy_avx2_prefetch(void *dest, const void *src, size_t len) {
  auto *d = static_cast<uint8_t *>(dest);
  const auto *s = static_cast<const uint8_t *>(src);
  constexpr size_t kPrefetchDist = 256;

  while (len >= 128) {
    _mm_prefetch(reinterpret_cast<const char *>(s + kPrefetchDist),
                 _MM_HINT_T0);
    _mm_prefetch(reinterpret_cast<const char *>(s + kPrefetchDist + 64),
                 _MM_HINT_T0);
    __m256i v0 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    __m256i v1 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 32));
    __m256i v2 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 64));
    __m256i v3 = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s + 96));
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d), v0);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 32), v1);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 64), v2);
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d + 96), v3);
    d += 128;
    s += 128;
    len -= 128;
  }
  while (len >= 32) {
    __m256i v = _mm256_loadu_si256(reinterpret_cast<const __m256i *>(s));
    _mm256_storeu_si256(reinterpret_cast<__m256i *>(d), v);
    d += 32;
    s += 32;
    len -= 32;
  }
  while (len--) {
    *d++ = *s++;
  }
  return dest;
}

#endif // defined(__x86_64__) || defined(_M_X64)

} // namespace memory_bench

#endif // MEMCP_BENCH_MEMORY_COPY_COPY_H