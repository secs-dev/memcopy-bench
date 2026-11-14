#include <array>
#include <cstddef>

#include "gtest/gtest.h"
#include "lib/buffers/abstract_buffer.hpp"

namespace buffers::abstract_buffer {
namespace {

using Packet = AbstractPacket<2, 4>;

constexpr std::array<std::byte, 6> kRawPacket = {
    std::byte{0x01}, std::byte{0x02}, std::byte{0xA1},
    std::byte{0xA2}, std::byte{0xA3}, std::byte{0xA4}};

TEST(AbstractPacketTest, CopyHeaderAndReturnPayloadInInputBuffer) {
  auto header = Packet::AbstractHeader();

  const auto* payload =
      Packet::stripHeader(kRawPacket.data(), kRawPacket.size(), header);

  EXPECT_EQ(header.data[0], kRawPacket[0]);
  EXPECT_EQ(header.data[1], kRawPacket[1]);
  EXPECT_EQ(payload, kRawPacket.data() + 2);
}

TEST(AbstractPacketTest, RejectNullBuffer) {
  auto header = Packet::AbstractHeader();

  EXPECT_EQ(Packet::stripHeader(nullptr, kRawPacket.size(), header), nullptr);
}

TEST(AbstractPacketTest, RejectTruncatedHeaderOrPayload) {
  std::array<std::byte, 1> kShortHeader = {std::byte{0x01}};
  std::array<std::byte, 5> kShortPayload = {
      std::byte{0x01}, std::byte{0x02}, std::byte{0xA1}, std::byte{0xA2},
      std::byte{0xA3}};
  auto header = Packet::AbstractHeader();

  EXPECT_EQ(
      Packet::stripHeader(kShortHeader.data(), kShortHeader.size(), header),
      nullptr);

  EXPECT_EQ(
      Packet::stripHeader(kShortPayload.data(), kShortPayload.size(), header),
      nullptr);
}

}  // namespace
}  // namespace buffers::abstract_buffer
