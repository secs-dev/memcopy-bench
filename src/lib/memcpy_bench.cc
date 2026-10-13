#include "lib/memcpy_bench.h"

#include <cstring>
#include <random>

#include <benchmark/benchmark.h>

namespace memcpy_bench {

namespace {

void BM_Memcpy(benchmark::State& state) {
  const auto size = static_cast<std::size_t>(state.range(0));

  const std::vector<unsigned char> src = RandomBytes(size);
  std::vector<unsigned char> dst(size);

  for (auto _ : state) {
    std::memcpy(dst.data(), src.data(), size);
    benchmark::DoNotOptimize(dst.data());
    benchmark::ClobberMemory();
  }

  state.SetBytesProcessed(static_cast<std::int64_t>(state.iterations()) *
                          static_cast<std::int64_t>(size));
}

}

std::vector<unsigned char> RandomBytes(std::size_t size, std::uint32_t seed) {
  std::mt19937 gen(seed);
  std::uniform_int_distribution<int> dist(0, 255);
  std::vector<unsigned char> bytes(size);
  for (auto& byte : bytes) {
    byte = static_cast<unsigned char>(dist(gen));
  }
  return bytes;
}

int Run(int argc, char** argv) {
  benchmark::RegisterBenchmark("BM_Memcpy", BM_Memcpy)
      ->RangeMultiplier(4)
      ->Range(64, 64 << 20);

  benchmark::Initialize(&argc, argv);
  if (benchmark::ReportUnrecognizedArguments(argc, argv)) {
    return 1;
  }
  benchmark::RunSpecifiedBenchmarks();
  benchmark::Shutdown();
  return 0;
}

}
