#include "lib/memory/copy/copy.h"
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

template <auto CopyFn>
void BM_Copy(benchmark::State& state) {
  const auto size = static_cast<std::size_t>(state.range(0));
  const std::vector<std::byte> source = MakeInput(size);
  std::vector<std::byte> destination(size);

  for (auto _ : state) {
    CopyFn(destination.data(), source.data(), size);
    benchmark::DoNotOptimize(destination.data());
    benchmark::ClobberMemory();
  }

  state.SetBytesProcessed(static_cast<std::int64_t>(state.iterations()) *
                          static_cast<std::int64_t>(size));
}

inline void* CopyNaiveWrapper(void* d, const void* s, std::size_t n) {
  return memory_bench::copy_naive(d, s, n);
}

}  // namespace

BENCHMARK(BM_Copy<memcopy_bench::memory::CopyWithMemcpy>)
    ->Name("BM_Memcpy")
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);

BENCHMARK(BM_Copy<CopyNaiveWrapper>)
    ->Name("BM_CopyNaive")
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);

BENCHMARK(BM_Copy<memory_bench::copy_duff>)
    ->Name("BM_CopyDuff")
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);

BENCHMARK(BM_Copy<memory_bench::copy_words64>)
    ->Name("BM_CopyWords64")
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);

BENCHMARK(BM_Copy<memory_bench::copy_words64_unrolled4>)
    ->Name("BM_CopyWords64Unrolled4")
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);

BENCHMARK(BM_Copy<memory_bench::copy_small_overlapping>)
    ->Name("BM_CopySmallOverlapping")
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);

#if defined(__x86_64__) || defined(_M_X64)
BENCHMARK(BM_Copy<memory_bench::copy_rep_movsb>)
    ->Name("BM_CopyRepMovsb")
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);

BENCHMARK(BM_Copy<memory_bench::copy_rep_movsq>)
    ->Name("BM_CopyRepMovsq")
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);

BENCHMARK(BM_Copy<memory_bench::copy_sse2>)
    ->Name("BM_CopySSE2")
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);

BENCHMARK(BM_Copy<memory_bench::copy_avx2>)
    ->Name("BM_CopyAVX2")
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);

BENCHMARK(BM_Copy<memory_bench::copy_avx2_stream>)
    ->Name("BM_CopyAVX2Stream")
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);

BENCHMARK(BM_Copy<memory_bench::copy_avx2_prefetch>)
    ->Name("BM_CopyAVX2Prefetch")
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);
#endif

BENCHMARK_MAIN();
