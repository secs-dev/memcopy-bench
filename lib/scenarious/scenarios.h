#pragma once

#include "lib/memory/copy/copy.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>

namespace memcopy_bench::scenarios {

// Scenarios are types exposing static void copy(void*, const void*, size_t).
// Nonempty ranges must be valid and source/destination must not overlap.
// Zero-length calls do nothing, including when passed null pointers.
template <auto CopyMethod> struct SimpleScenario {
  static void copy(void *dst, const void *src, std::size_t length) {
    if (length != 0) {
      CopyMethod(dst, src, length);
    }
  }
};

namespace detail {

template <class HeadScenario, class BodyScenario, class TailScenario,
          std::size_t Alignment, bool AlignSourcePointer>
struct AlignScenario {
  static_assert(Alignment > 0, "Alignment must be positive");

  static void copy(void *dst, const void *src, std::size_t length) {
    if (length == 0) {
      return;
    }

    auto *target = static_cast<std::byte *>(dst);
    const auto *source = static_cast<const std::byte *>(src);
    const void *aligned_pointer = AlignSourcePointer ? src : dst;
    const auto offset =
        reinterpret_cast<std::uintptr_t>(aligned_pointer) % Alignment;
    const std::size_t prefix = offset == 0 ? 0 : Alignment - offset;
    const std::size_t head = std::min(length, prefix);
    const std::size_t remaining = length - head;
    const std::size_t body = remaining - remaining % Alignment;
    const std::size_t tail = remaining - body;

    if (head != 0) {
      HeadScenario::copy(target, source, head);
    }
    if (body != 0) {
      BodyScenario::copy(target + head, source + head, body);
    }
    if (tail != 0) {
      TailScenario::copy(target + head + body, source + head + body, tail);
    }
  }
};

} // namespace detail

// Body starts at an aligned source address and has an Alignment-multiple
// length. Destination alignment is independent. Children may split their ranges
// again.
template <class HeadScenario, class BodyScenario, class TailScenario,
          std::size_t Alignment>
struct AlignSource : detail::AlignScenario<HeadScenario, BodyScenario,
                                           TailScenario, Alignment, true> {};

// Body starts at an aligned destination address; source may remain unaligned.
template <class HeadScenario, class BodyScenario, class TailScenario,
          std::size_t Alignment>
struct AlignTarget : detail::AlignScenario<HeadScenario, BodyScenario,
                                           TailScenario, Alignment, false> {};

// Selection uses this node's length, which may be a parent's body length.
// Equality belongs to LongScenario.
template <class ShortScenario, std::size_t Threshold, class LongScenario>
struct ShortFallback {
  static void copy(void *dst, const void *src, std::size_t length) {
    if (length == 0) {
      return;
    }
    if (length < Threshold) {
      ShortScenario::copy(dst, src, length);
    } else {
      LongScenario::copy(dst, src, length);
    }
  }
};

#if defined(__x86_64__) || defined(_M_X64)
// "Above" includes MinLength: this is the minimum prefetched copy length.
// Reuses existing AVX2 implementations, including their 4x unrolling and tails.
// Caller must ensure AVX2 support; there is no runtime CPU dispatch here.
template <std::size_t MinLength> struct AVX2PrefetchAbove {
  static void copy(void *dst, const void *src, std::size_t length) {
    if (length == 0) {
      return;
    }
    if (length >= MinLength) {
      memory_bench::copy_avx2_prefetch(dst, src, length);
    } else {
      memory_bench::copy_avx2(dst, src, length);
    }
  }
};

// Composed hybrid scenario selecting the verified optimal inline copy method
// per payload regime without calling std::memcpy:
// - < 32 B: branchless overlapping scalar copy
// - 32..256 B: inline unaligned AVX2 copy
// - 257 B..<2 KiB: inline 32B-aligned-dst 4x-unrolled AVX2 temporal copy
// - 2 KiB..<32 KiB: std::memcpy (glibc hand-tuned L1d loop)
// - 32 KiB..2 MiB: inline x86 ERMS/FSRM (rep movsb)
// - > 2 MiB: 64B-aligned-dst 8x-unrolled AVX2 non-temporal streaming copy
using VioletCopy = ShortFallback<
    SimpleScenario<memory_bench::copy_small_overlapping>, 32,
    ShortFallback<
        SimpleScenario<memory_bench::copy_avx2_l1_inline>, 257,
        ShortFallback<
            SimpleScenario<memory_bench::copy_avx2_aligned_dst>, 2048,
            ShortFallback<
                SimpleScenario<memory_bench::copy_memcpy>, 32768,
                ShortFallback<
                    SimpleScenario<memory_bench::copy_rep_movsb>, 2097153,
                    SimpleScenario<memory_bench::copy_avx2_stream_8x>>>>>>;
#endif

} // namespace memcopy_bench::scenarios
