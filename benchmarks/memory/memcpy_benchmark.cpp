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

} // namespace

template <auto Fn>
void RegisterBMCopy(const char *name)
{
  benchmark::RegisterBenchmark(name, BM_Copy<Fn>)
    ->RangeMultiplier(4)
    ->Range(64, 64 << 20);
}

static const bool Registered = []
{
  RegisterBMCopy<memcopy_bench::memory::CopyWithMemcpy>("BM_Memcpy");
  RegisterBMCopy<CopyNaiveWrapper>("BM_CopyNaive");
  RegisterBMCopy<memory_bench::copy_duff>("BM_CopyDuff");
  RegisterBMCopy<memory_bench::copy_words64>("BM_CopyWords64");
  RegisterBMCopy<memory_bench::copy_words64_unrolled4>("BM_CopyWords64Unrolled4");
  RegisterBMCopy<memory_bench::copy_small_overlapping>("BM_CopySmallOverlapping");
  return true;
}();

#if defined(__x86_64__) || defined(_M_X64)
static const bool x86Registered = []
{
  RegisterBMCopy<memory_bench::copy_rep_movsb>("BM_CopyRepMovsb");
  RegisterBMCopy<memory_bench::copy_rep_movsq>("BM_CopyRepMovsq");
  RegisterBMCopy<memory_bench::copy_sse2>("BM_CopySSE2");
  RegisterBMCopy<memory_bench::copy_avx2>("BM_CopyAVX2");
  RegisterBMCopy<memory_bench::copy_avx2_stream>("BM_CopyAVX2Stream");
  RegisterBMCopy<memory_bench::copy_avx2_prefetch>("BM_CopyAVX2Prefetch");
  return true;
}();
#endif

BENCHMARK_MAIN();
