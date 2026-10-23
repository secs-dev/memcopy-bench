#pragma once

#include <cstddef>

namespace memcopy_bench::memory {

// Copies exactly size bytes from source to destination using std::memcpy.
void CopyWithMemcpy(void* destination, const void* source, std::size_t size);

}  // namespace memcopy_bench::memory
