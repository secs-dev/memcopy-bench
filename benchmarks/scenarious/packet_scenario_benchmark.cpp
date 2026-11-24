#include "lib/buffers/abstract_buffer.hpp"
#include "lib/memory/copy/copy.h"
#include "lib/scenarious/scenarios.h"

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include <benchmark/benchmark.h>

namespace {

namespace sc = memcopy_bench::scenarios;

std::vector<std::byte> MakeInput(std::size_t size) {
  std::vector<std::byte> input(size);
  std::uint32_t state = 0xC0FFEEu;
  for (std::byte &value : input) {
    state = state * 1664525u + 1013904223u;
    value = static_cast<std::byte>(state >> 24);
  }
  return input;
}

// Compile-time composition of Scenario, header size, and payload size.
// Scenario::copy is inlined down to the concrete memory method because all
// three are compile-time parameters.
template <class Scenario, std::size_t HeaderLen, std::size_t PayloadLen>
void BM_PacketScenario(benchmark::State &state) {
  using Packet =
      buffers::abstract_buffer::AbstractPacket<HeaderLen, PayloadLen>;
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

    Scenario::copy(destination.data(), payload, PayloadLen);
    benchmark::DoNotOptimize(header);
    benchmark::DoNotOptimize(destination.data());
    benchmark::ClobberMemory();
  }

  state.SetBytesProcessed(static_cast<std::int64_t>(state.iterations()) *
                          static_cast<std::int64_t>(HeaderLen + PayloadLen));
}

// Registers one benchmark per payload size. Name layout
// BM_PacketScenario/<name>/h<H>/p<P> is a stable contract for
// --benchmark_filter and for the Python orchestration layer.
template <class Scenario, std::size_t HeaderLen, std::size_t... PayloadLens>
void RegisterScenario(const char *name) {
  const std::string prefix = std::string("BM_PacketScenario/") + name + "/h" +
                             std::to_string(HeaderLen) + "/p";
  (benchmark::RegisterBenchmark(
       (prefix + std::to_string(PayloadLens)).c_str(),
       BM_PacketScenario<Scenario, HeaderLen, PayloadLens>),
   ...);
}

// Two header sizes cover common telecom cases (GTP-U basic, plus room for an
// extension header). Payload sizes are compile-time powers of two from 64 B
// (one cache line) through 32 KiB (2^15), covering L1/L2/L3 transitions.
template <class Scenario> void RegisterScenarioBothH(const char *name) {
  RegisterScenario<Scenario, 8, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
                   16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608, 16777216, 33554432>(name);
  RegisterScenario<Scenario, 16, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
                   16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608, 16777216, 33554432>(name);
}

// Scenario whitelist. Extend as new combinations are studied. Each entry adds
// |H| * |P| benchmarks.
using Naive = sc::SimpleScenario<memory_bench::copy_naive>;
using Baseline = sc::SimpleScenario<memory_bench::copy_memcpy>;
using Small = sc::SimpleScenario<memory_bench::copy_small_overlapping>;
using Words64 = sc::SimpleScenario<memory_bench::copy_words64>;

#if defined(__x86_64__) || defined(_M_X64)
using SSE2 = sc::SimpleScenario<memory_bench::copy_sse2>;
using Prefetch = sc::AVX2PrefetchAbove<1024>;
using Stream = sc::SimpleScenario<memory_bench::copy_avx2_stream>;
using MemcpyEdgesSSEBody = sc::AlignTarget<Baseline, SSE2, Baseline, 16>;
using MemcpyEdgesAVXBody = sc::AlignTarget<Baseline, Prefetch, Baseline, 32>;
using SSEEdgesMemcpyBody = sc::AlignTarget<SSE2, Baseline, SSE2, 16>;
using AVXEdgesMemcpyBody = sc::AlignTarget<Prefetch, Baseline, Prefetch, 32>;
using ComposedPrefetch = sc::ShortFallback<
    Baseline, 256, sc::AlignTarget<Baseline, Prefetch, Baseline, 32>>;
using ComposedStream = sc::ShortFallback<
    Baseline, 256, sc::AlignTarget<Baseline, Stream, Baseline, 32>>;
using ComposedSSE =
    sc::ShortFallback<Baseline, 64,
                      sc::AlignTarget<Baseline, SSE2, Baseline, 16>>;
#endif

} // namespace

static const bool RegisteredScenarios = [] {
  RegisterScenarioBothH<Naive>("Naive");
  RegisterScenarioBothH<Baseline>("Memcpy");
  RegisterScenarioBothH<Small>("Small");
  RegisterScenarioBothH<Words64>("Words64");

#if ((defined(__x86_64__) || defined(_M_X64)) && (defined(__GNUC__) || defined(__clang__)))
  // Safety guard only. The full CPU feature registry (with "skipped" records in
  // the report) is a separate follow-up. Without this check, running on a host
  // that lacks AVX2 would SIGILL inside AVX2PrefetchAbove.
  if (__builtin_cpu_supports("avx2")) {
    RegisterScenarioBothH<ComposedPrefetch>("ComposedPrefetch");
    RegisterScenarioBothH<ComposedStream>("ComposedStream");
    RegisterScenarioBothH<MemcpyEdgesAVXBody>("MemcpyEdgesAVXBody");
    RegisterScenarioBothH<AVXEdgesMemcpyBody>("AVXEdgesMemcpyBody");
  }
  if (__builtin_cpu_supports("sse2")) {
    RegisterScenarioBothH<SSEEdgesMemcpyBody>("SSEEdgesMemcpyBody");
    RegisterScenarioBothH<ComposedSSE>("ComposedSSE");
    RegisterScenarioBothH<MemcpyEdgesSSEBody>("MemcpyEdgesSSEBody");
  }
#endif

  return true;
}();

BENCHMARK_MAIN();