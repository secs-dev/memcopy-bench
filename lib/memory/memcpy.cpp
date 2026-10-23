#include "lib/memory/memcpy.h"

#include <cstring>

namespace memcopy_bench::memory {

void CopyWithMemcpy(void* destination, const void* source, std::size_t size) {
  std::memcpy(destination, source, size);
}

}  // namespace memcopy_bench::memory
