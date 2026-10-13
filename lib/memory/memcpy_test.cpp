#include "lib/memory/memcpy.h"

#include <array>
#include <cstddef>

#include "gtest/gtest.h"

namespace memcopy_bench::memory {
namespace {

TEST(CopyWithMemcpyTest, CopiesRequestedBytes) {
  constexpr std::array<std::byte, 4> source = {
      std::byte{0x01}, std::byte{0x02}, std::byte{0x03}, std::byte{0x04}};
  std::array<std::byte, 4> destination{};

  CopyWithMemcpy(destination.data(), source.data(), source.size());

  EXPECT_EQ(destination, source);
}

TEST(CopyWithMemcpyTest, DoesNotWritePastRequestedSize) {
  constexpr std::array<std::byte, 4> source = {
      std::byte{0x01}, std::byte{0x02}, std::byte{0x03}, std::byte{0x04}};
  std::array<std::byte, 4> destination = {
      std::byte{0xFF}, std::byte{0xFF}, std::byte{0xFF}, std::byte{0xFF}};

  CopyWithMemcpy(destination.data(), source.data(), 2);

  EXPECT_EQ(destination[0], source[0]);
  EXPECT_EQ(destination[1], source[1]);
  EXPECT_EQ(destination[2], std::byte{0xFF});
  EXPECT_EQ(destination[3], std::byte{0xFF});
}

}  // namespace
}  // namespace memcopy_bench::memory
