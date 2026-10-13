#include "lib/memory/memcpy.h"

#include <cstddef>
#include <cstdint>
#include <vector>

#include <benchmark/benchmark.h>

namespace {

std::vector<std::byte> MakeInput(std::size_t size) {
  std::vector<std::byte> input(size);
  std::uint32_t state = 0xC0FFEEu;
  for (std::byte& value : input) {
    state = state * 1664525u + 1013904223u;
    value = static_cast<std::byte>(state >> 24);
  }
  return input;
}

void BM_Memcpy(benchmark::State& state) {
  const auto size = static_cast<std::size_t>(state.range(0));
  const std::vector<std::byte> source = MakeInput(size);
  std::vector<std::byte> destination(size);

  for (auto _ : state) {
    memcopy_bench::memory::CopyWithMemcpy(
        destination.data(), source.data(), size);
    benchmark::DoNotOptimize(destination.data());
    benchmark::ClobberMemory();
  }

  state.SetBytesProcessed(static_cast<std::int64_t>(state.iterations()) *
                          static_cast<std::int64_t>(size));
}

BENCHMARK(BM_Memcpy)->RangeMultiplier(4)->Range(64, 64 << 20);

}  // namespace

BENCHMARK_MAIN();
