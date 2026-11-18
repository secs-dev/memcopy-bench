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
// extension header). Payload sizes span cache levels and jumbo frames.
template <class Scenario> void RegisterScenarioBothH(const char *name) {
  RegisterScenario<Scenario, 8, 64, 128, 512, 1500, 4096, 9000>(name);
  RegisterScenario<Scenario, 16, 64, 128, 512, 1500, 4096, 9000>(name);
}

// Scenario whitelist. Extend as new combinations are studied. Each entry adds
// |H| * |P| benchmarks.
using Baseline = sc::SimpleScenario<memory_bench::copy_memcpy>;
using Small = sc::SimpleScenario<memory_bench::copy_small_overlapping>;
using Words64 = sc::SimpleScenario<memory_bench::copy_words64>;

#if defined(__x86_64__) || defined(_M_X64)
using ComposedPrefetch =
    sc::ShortFallback<Small, 64,
                      sc::AlignTarget<Small, sc::AVX2PrefetchAbove<1024>, Small,
                                      32>>;
#endif

} // namespace

static const bool RegisteredScenarios = [] {
  RegisterScenarioBothH<Baseline>("Memcpy");
  RegisterScenarioBothH<Small>("Small");
  RegisterScenarioBothH<Words64>("Words64");

#if defined(__x86_64__) && (defined(__GNUC__) || defined(__clang__))
  // Safety guard only. The full CPU feature registry (with "skipped" records in
  // the report) is a separate follow-up. Without this check, running on a host
  // that lacks AVX2 would SIGILL inside AVX2PrefetchAbove.
  if (__builtin_cpu_supports("avx2")) {
    RegisterScenarioBothH<ComposedPrefetch>("ComposedPrefetch");
  }
#endif

  return true;
}();

BENCHMARK_MAIN();