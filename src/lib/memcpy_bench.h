#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace memcpy_bench {

std::vector<unsigned char> RandomBytes(std::size_t size, std::uint32_t seed = 42);

int Run(int argc, char** argv);

}
