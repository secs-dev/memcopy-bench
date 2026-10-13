#include "gtest/gtest.h"
#include "lib/memcpy_bench.h"

TEST(RandomBytesTest, ReturnsRequestedSize) {
  EXPECT_EQ(memcpy_bench::RandomBytes(0).size(), 0u);
  EXPECT_EQ(memcpy_bench::RandomBytes(1024).size(), 1024u);
}

TEST(RandomBytesTest, SameSeedGivesSameBytes) {
  EXPECT_EQ(memcpy_bench::RandomBytes(256, 7), memcpy_bench::RandomBytes(256, 7));
}

TEST(RandomBytesTest, DifferentSeedsGiveDifferentBytes) {
  EXPECT_NE(memcpy_bench::RandomBytes(256, 1), memcpy_bench::RandomBytes(256, 2));
}
