#include "lib/scenarious/scenarios.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <utility>
#include <vector>

#include "gtest/gtest.h"

namespace memcopy_bench::scenarios {
namespace {

using Small = SimpleScenario<memory_bench::copy_small_overlapping>;
using CopyFn = void (*)(void *, const void *, std::size_t);

struct ScenarioCase {
  const char *name;
  CopyFn copy;
  bool needs_avx2 = false;
};

class ScenariosTest : public ::testing::TestWithParam<ScenarioCase> {};

TEST_P(ScenariosTest, CopiesAcrossSizesAndAlignmentsWithoutChangingGuards) {
  const auto &scenario = GetParam();
#if defined(__x86_64__) && (defined(__GNUC__) || defined(__clang__))
  if (scenario.needs_avx2 && !__builtin_cpu_supports("avx2")) {
    GTEST_SKIP() << "AVX2 is unavailable";
  }
#endif
  const std::vector<std::size_t> sizes = {
      0,  1,   2,   7,   15,  16,  17,  31,   32,   33,   63,   64,
      65, 127, 128, 129, 255, 256, 257, 1023, 1024, 1025, 1500, 4096};
  for (std::size_t size : sizes) {
    for (std::size_t source_offset : {0, 1, 7, 15, 31}) {
      for (std::size_t target_offset : {0, 1, 7, 15, 31}) {
        SCOPED_TRACE(::testing::Message()
                     << scenario.name << " size=" << size
                     << " src=" << source_offset << " dst=" << target_offset);
        // Extra readable padding also accommodates existing prefetch methods.
        std::vector<std::uint8_t> source(size + 512);
        for (std::size_t i = 0; i < source.size(); ++i) {
          source[i] = static_cast<std::uint8_t>((i * 109 + 23) & 0xFF);
        }
        const auto original_source = source;
        std::vector<std::uint8_t> target(size + 64, 0xEE);
        auto expected = target;
        std::memcpy(expected.data() + target_offset,
                    source.data() + source_offset, size);
        scenario.copy(target.data() + target_offset,
                      source.data() + source_offset, size);
        EXPECT_EQ(target, expected);
        EXPECT_EQ(source, original_source);
      }
    }
  }
  scenario.copy(nullptr, nullptr, 0);
}

const ScenarioCase kScenarios[] = {
    {"Simple", Small::copy},
    {"AlignSource", AlignSource<Small, Small, Small, 32>::copy},
    {"AlignTarget", AlignTarget<Small, Small, Small, 32>::copy},
    {"AlignmentOne", AlignSource<Small, Small, Small, 1>::copy},
    {"NonPowerOfTwo", AlignTarget<Small, Small, Small, 3>::copy},
    {"ShortFallback", ShortFallback<Small, 64, Small>::copy},
    {"Nested",
     ShortFallback<Small, 64,
                   AlignSource<Small, AlignTarget<Small, Small, Small, 16>,
                               Small, 32>>::copy},
#if defined(__x86_64__) || defined(_M_X64)
    {"Prefetch", AVX2PrefetchAbove<1024>::copy, true},
    {"ComposedPrefetch",
     ShortFallback<
         Small, 64,
         AlignTarget<Small, AVX2PrefetchAbove<1024>, Small, 32>>::copy,
     true},
#endif
};

INSTANTIATE_TEST_SUITE_P(ScenarioCopies, ScenariosTest,
                         ::testing::ValuesIn(kScenarios));

// Distinct copying children make incorrect routing observable in the output.
template <unsigned char Value> struct FillScenario {
  static void copy(void *dst, const void *, std::size_t length) {
    std::memset(dst, Value, length);
  }
};

using Head = FillScenario<1>;
using Body = FillScenario<2>;
using Tail = FillScenario<3>;

TEST(AlignmentScenarioTest, SplitsAtSourceBoundary) {
  alignas(32) std::array<unsigned char, 128> source{};
  alignas(32) std::array<unsigned char, 128> target{};
  AlignSource<Head, Body, Tail, 32>::copy(target.data(), source.data() + 1, 66);
  for (std::size_t i = 0; i < target.size(); ++i) {
    const int expected = i < 31 ? 1 : i < 63 ? 2 : i < 66 ? 3 : 0;
    EXPECT_EQ(target[i], expected) << "index=" << i;
  }
}

TEST(AlignmentScenarioTest, SplitsAtTargetBoundary) {
  alignas(32) std::array<unsigned char, 128> source{};
  alignas(32) std::array<unsigned char, 128> target{};
  AlignTarget<Head, Body, Tail, 32>::copy(target.data() + 7, source.data(), 60);
  for (std::size_t i = 0; i < target.size(); ++i) {
    const int expected = i < 7 ? 0 : i < 32 ? 1 : i < 64 ? 2 : i < 67 ? 3 : 0;
    EXPECT_EQ(target[i], expected) << "index=" << i;
  }
}

TEST(ShortFallbackTest, EqualitySelectsLongScenario) {
  using Scenario = ShortFallback<Head, 32, Body>;
  std::array<unsigned char, 33> source{};
  for (std::size_t length : {31, 32, 33}) {
    std::array<unsigned char, 33> target{};
    Scenario::copy(target.data(), source.data(), length);
    for (std::size_t i = 0; i < target.size(); ++i) {
      EXPECT_EQ(target[i], i >= length ? 0 : length < 32 ? 1 : 2);
    }
  }
}

} // namespace
} // namespace memcopy_bench::scenarios
