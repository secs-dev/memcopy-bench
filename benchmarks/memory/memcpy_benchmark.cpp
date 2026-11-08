#include "lib/buffers/abstract_buffer.hpp"
#include "lib/memory/copy/copy.h"
#include "lib/memory/memcpy.h"

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include <benchmark/benchmark.h>

namespace {

std::vector<std::byte> MakeInput(std::size_t size) {
  std::vector<std::byte> input(size);
  std::uint32_t state = 0xC0FFEEu;
  for (std::byte &value : input) {
    state = state * 1664525u + 1013904223u;
    value = static_cast<std::byte>(state >> 24);
  }
  return input;
}

template <auto CopyFn> void BM_Copy(benchmark::State &state) {
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

template <auto CopyFn, size_t Len> void BM_CopyFixed(benchmark::State &state) {
  const auto size = static_cast<std::size_t>(Len);
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

template <auto CopyFn, std::size_t HeaderLen, std::size_t PayloadLen>
void BM_CopyPacket(benchmark::State &state) {
  using Packet = buffers::abstract_buffer::AbstractPacket<HeaderLen, PayloadLen>;
  typename Packet::AbstractHeader header;
  const std::vector<std::byte> source = MakeInput(HeaderLen + PayloadLen);
  std::vector<std::byte> destination(PayloadLen);

  for (auto _ : state) {
    const auto *payload =
        Packet::stripHeader(source.data(), source.size(), header);

    if (payload == nullptr) {
      state.SkipWithError("Failed to strip the packet header");
      return;
    }

    CopyFn(destination.data(), payload, PayloadLen);
    benchmark::DoNotOptimize(header);
    benchmark::DoNotOptimize(destination.data());
    benchmark::ClobberMemory();
  }

  state.SetBytesProcessed(static_cast<std::int64_t>(state.iterations()) *
                          static_cast<std::int64_t>(HeaderLen + PayloadLen));
}

inline void *CopyNaiveWrapper(void *d, const void *s, std::size_t n) {
  return memory_bench::copy_naive(d, s, n);
}

} // namespace

template <auto Fn> void RegisterBMCopy(const char *name) {
  benchmark::RegisterBenchmark(name, BM_Copy<Fn>)
      ->RangeMultiplier(4)
      ->Range(64, 64 << 20);
}

template <auto Fn, std::size_t Cur, std::size_t Max, std::size_t Mult>
void RegisterBMCopyFixed(const char *name) {
  std::string full_name = std::string(name) + "/" + std::to_string(Cur);
  benchmark::RegisterBenchmark(full_name.c_str(), BM_CopyFixed<Fn, Cur>);
  if constexpr (Cur * Mult <= Max) {
    RegisterBMCopyFixed<Fn, Cur * Mult, Max, Mult>(name);
  }
}

template <auto Fn> void RegisterAllBMCopyFixed(const char *name) {
  RegisterBMCopyFixed<Fn, 64, 64 << 20, 4>(name);
}

template <auto Fn, std::size_t HeaderLen, std::size_t... PayloadLens>
void RegisterBMCopyPacket(const char *name) {
  const std::string prefix =
      std::string(name) + "/" + std::to_string(HeaderLen) + "/";
  (benchmark::RegisterBenchmark(
       (prefix + std::to_string(PayloadLens)).c_str(),
       BM_CopyPacket<Fn, HeaderLen, PayloadLens>),
   ...);
}

static const bool Registered = [] {
  RegisterBMCopy<memcopy_bench::memory::CopyWithMemcpy>("BM_Memcpy");
  RegisterBMCopy<CopyNaiveWrapper>("BM_CopyNaive");
  RegisterBMCopy<memory_bench::copy_duff>("BM_CopyDuff");
  RegisterBMCopy<memory_bench::copy_words64>("BM_CopyWords64");
  RegisterBMCopy<memory_bench::copy_words64_unrolled4>(
      "BM_CopyWords64Unrolled4");
  RegisterBMCopy<memory_bench::copy_small_overlapping>(
      "BM_CopySmallOverlapping");
  return true;
}();

static const bool RegisteredFixed = [] {
  RegisterAllBMCopyFixed<memcopy_bench::memory::CopyWithMemcpy>(
      "BM_Memcpy_Fixed");
  RegisterAllBMCopyFixed<CopyNaiveWrapper>("BM_CopyNaive_Fixed");
  RegisterAllBMCopyFixed<memory_bench::copy_duff>("BM_CopyDuff_Fixed");
  RegisterAllBMCopyFixed<memory_bench::copy_words64>("BM_CopyWords64_Fixed");
  RegisterAllBMCopyFixed<memory_bench::copy_words64_unrolled4>(
      "BM_CopyWords64Unrolled4_Fixed");
  RegisterAllBMCopyFixed<memory_bench::copy_small_overlapping>(
      "BM_CopySmallOverlapping_Fixed");
  return true;
}();

static const bool RegisteredFixedPackets = [] {
    RegisterBMCopyPacket<memcopy_bench::memory::CopyWithMemcpy, 16, 64, 512, 1500>(
      "BM_CopyPacket_Memcpy");
    RegisterBMCopyPacket<CopyNaiveWrapper, 16, 64, 512, 1500>(
      "BM_CopyPacket_Naive");
  return true;
}();

#if defined(__x86_64__) || defined(_M_X64)
static const bool x86Registered = [] {
  RegisterBMCopy<memory_bench::copy_rep_movsb>("BM_CopyRepMovsb");
  RegisterBMCopy<memory_bench::copy_rep_movsq>("BM_CopyRepMovsq");
  RegisterBMCopy<memory_bench::copy_sse2>("BM_CopySSE2");
  RegisterBMCopy<memory_bench::copy_avx2>("BM_CopyAVX2");
  RegisterBMCopy<memory_bench::copy_avx2_stream>("BM_CopyAVX2Stream");
  RegisterBMCopy<memory_bench::copy_avx2_prefetch>("BM_CopyAVX2Prefetch");
  return true;
}();

static const bool x86RegisteredFixed = [] {
  RegisterAllBMCopyFixed<memory_bench::copy_rep_movsb>("BM_CopyRepMovsb_Fixed");
  RegisterAllBMCopyFixed<memory_bench::copy_rep_movsq>("BM_CopyRepMovsq_Fixed");
  RegisterAllBMCopyFixed<memory_bench::copy_sse2>("BM_CopySSE2_Fixed");
  RegisterAllBMCopyFixed<memory_bench::copy_avx2>("BM_CopyAVX2_Fixed");
  RegisterAllBMCopyFixed<memory_bench::copy_avx2_stream>(
      "BM_CopyAVX2Stream_Fixed");
  RegisterAllBMCopyFixed<memory_bench::copy_avx2_prefetch>(
      "BM_CopyAVX2Prefetch_Fixed");
  return true;
}();
#endif

BENCHMARK_MAIN();
