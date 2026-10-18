#include "lib/memory/copy/copy.h"

#include <cstddef>
#include <cstdint>
#include <vector>

#include "gtest/gtest.h"

namespace memory_bench {
namespace {

using CopyFn = void* (*)(void*, const void*, std::size_t);

class CopyImplementationsTest : public ::testing::TestWithParam<std::pair<const char*, CopyFn>> {};

TEST_P(CopyImplementationsTest, CopiesCorrectlyAcrossSizesAndAlignments) {
  auto [name, copy_func] = GetParam();
  const std::vector<std::size_t> test_sizes = {
      0, 1, 2, 3, 4, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65, 127, 128, 129, 255, 256, 1024, 4096};

  for (std::size_t size : test_sizes) {
    for (std::size_t offset_src : {0, 1, 7, 15, 31}) {
      for (std::size_t offset_dst : {0, 1, 7, 15, 31}) {
        std::vector<std::uint8_t> src(size + 64);
        std::vector<std::uint8_t> dst(size + 64, 0xEE);

        for (std::size_t i = 0; i < src.size(); ++i) {
          src[i] = static_cast<std::uint8_t>((i * 109 + 23) & 0xFF);
        }

        void* ret = copy_func(dst.data() + offset_dst, src.data() + offset_src, size);
        EXPECT_EQ(ret, dst.data() + offset_dst) << "Failed return pointer for " << name;

        if (size > 0) {
          std::vector<std::uint8_t> expected(src.begin() + offset_src, src.begin() + offset_src + size);
          std::vector<std::uint8_t> actual(dst.begin() + offset_dst, dst.begin() + offset_dst + size);
          EXPECT_EQ(actual, expected)
              << "Mismatch for " << name << " size=" << size
              << " off_src=" << offset_src << " off_dst=" << offset_dst;
        }

        // Verify guard bytes before and after dst are intact
        for (std::size_t i = 0; i < offset_dst; ++i) {
          EXPECT_EQ(dst[i], 0xEE) << "Underflow guard overwrite for " << name;
        }
        for (std::size_t i = offset_dst + size; i < dst.size(); ++i) {
          EXPECT_EQ(dst[i], 0xEE) << "Overflow guard overwrite for " << name;
        }
      }
    }
  }
}

const std::pair<const char*, CopyFn> kImplementations[] = {
    {"copy_memcpy", copy_memcpy},
    {"copy_naive", [](void* d, const void* s, std::size_t l) { return copy_naive(d, s, l); }},
    {"copy_duff", copy_duff},
    {"copy_words64", copy_words64},
    {"copy_words64_unrolled4", copy_words64_unrolled4},
    {"copy_small_overlapping", copy_small_overlapping},
#if defined(__x86_64__) || defined(_M_X64)
    {"copy_rep_movsb", copy_rep_movsb},
    {"copy_rep_movsq", copy_rep_movsq},
    {"copy_sse2", copy_sse2},
    {"copy_avx2", copy_avx2},
    {"copy_avx2_stream", copy_avx2_stream},
    {"copy_avx2_prefetch", copy_avx2_prefetch},
#endif
};

INSTANTIATE_TEST_SUITE_P(CopyTests, CopyImplementationsTest, ::testing::ValuesIn(kImplementations));

TEST(CompileTimeCopyTest, TemplateNaiveAndFixed) {
  constexpr std::size_t kSize = 16;
  std::uint8_t src[kSize] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};
  std::uint8_t dst1[kSize] = {0};
  std::uint8_t dst2[kSize] = {0};

  copy_naive<kSize>(dst1, src);
  EXPECT_EQ(std::memcmp(dst1, src, kSize), 0);

  copy_fixed<kSize>(dst2, src);
  EXPECT_EQ(std::memcmp(dst2, src, kSize), 0);
}

}  // namespace
}  // namespace memory_bench
