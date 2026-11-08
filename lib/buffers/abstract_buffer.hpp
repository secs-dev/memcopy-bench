#pragma once

#include <algorithm>
#include <cstddef>
#include <limits>

namespace buffers::abstract_buffer {

/// @brief Fixed-size packet layout.
/// @tparam N header bytes
/// @tparam M payload (user data) bytes
template <std::size_t N, std::size_t M>
class AbstractPacket {
    static_assert(N > 0, "N must be greater than 0");
    static_assert(M > 0, "M must be greater than 0");

 public:
  struct AbstractHeader {
    std::byte data[N] { std::byte(0) };
  };

  /// Parses the header and returns a pointer to the payload in the buffer.
  /// @param rawPtr Start of the input buffer.
  /// @param rawSize Number of readable bytes starting at rawPtr.
  /// @param header Packet header.
  /// @return Pointer to packet payload on success or nullptr otherwise.
  static const std::byte* stripHeader(const std::byte* rawPtr,
                                      std::size_t rawSize,
                                      AbstractHeader& header) {
    if (rawPtr == nullptr || rawSize < N + M) {
      return nullptr;
    }

    for (unsigned i = 0; i < N; i++) {
        header.data[i] = rawPtr[i];
    }

    return rawPtr + N;
  }
};

} // namespace buffers::abstract_buffer
