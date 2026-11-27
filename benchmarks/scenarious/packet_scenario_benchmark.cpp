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

    /**
     * @brief Создает массив входных данных со случайными значениями.
     *
     * @details
     * Значения в создаваемом массиве одинаковы при каждом вызове
     * @param size Размер создаваемого массива.
     * @return Массив размера size заполненный случайными значениями
     */
    std::vector<std::byte> MakeInput(const std::size_t size) {
        std::vector<std::byte> input(size);
        std::uint32_t state = 0xC0FFEEu;
        for (std::byte &value: input) {
            state = state * 1664525u + 1013904223u;
            value = static_cast<std::byte>(state >> 24);
        }
        return input;
    }

    /**
     * @brief Основная функция бенчмарка. Осуществляет копирование тела пакета через композитор сценариев.
     *
     * @details
     * Строит синтетический пакет из @p HeaderLen байт заголовка и
     * @p PayloadLen байт тела пакета, извлекает заголовок через
     * @c AbstractPacket::stripHeader и копирует тело в отдельный буфер
     * через @c Scenario::copy. Все параметры заданы на этапе компиляции,
     * поэтому копирование инлайнится до конкретного метода.
     *
     * Для измерения копирования используется один и тот же заранее
     * подготовленный пакет, поэтому получаемые значения в большой степени
     * опираются на **горячий кэш**.
     *
     * @tparam Scenario   Сценарий из @c memcopy_bench::scenarios со
     *                    статическим методом @c copy.
     * @tparam HeaderLen  Размер заголовка в байтах (> 0).
     * @tparam PayloadLen Размер тела пакета в байтах (> 0).
     *
     * @param state Состояние Google Benchmark.
     */
    template<class Scenario, std::size_t HeaderLen, std::size_t PayloadLen>
    void BM_PacketScenario(benchmark::State &state) {
        using Packet =
                buffers::abstract_buffer::AbstractPacket<HeaderLen, PayloadLen>;
        typename Packet::AbstractHeader header;
        const std::vector<std::byte> source = MakeInput(HeaderLen + PayloadLen);
        std::vector<std::byte> destination(PayloadLen);

        for (auto _: state) {
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

    /**
     * @brief Регистрирует по одному бенчмарку на каждый размер тела пакета.
     *
     * @details
     * Для каждого значения из @p PayloadLens создаёт отдельный бенчмарк
     * @code BM_PacketScenario<Scenario, HeaderLen, P>@endcode под именем
     * @code BM_PacketScenario/<name>/h<HeaderLen>/p<P>@endcode.
     * Имя — стабильный контракт
     * для @c --benchmark_filter и для Python-оркестратора.
     *
     * @tparam Scenario    Тип сценария из @c memcopy_bench::scenarios.
     * @tparam HeaderLen   Размер заголовка в байтах (> 0).
     * @tparam PayloadLens Набор размеров тела запроса для регистрации (> 0).
     *
     * @param name Короткое имя сценария, попадающее в имя бенчмарка
     *             (например, @c "Memcpy", @c "ComposedPrefetch").
     */
    template<class Scenario, std::size_t HeaderLen, std::size_t... PayloadLens>
    void RegisterScenario(const char *name) {
        const std::string prefix = std::string("BM_PacketScenario/") + name + "/h" +
                                   std::to_string(HeaderLen) + "/p";
        (benchmark::RegisterBenchmark(
                (prefix + std::to_string(PayloadLens)).c_str(),
                BM_PacketScenario<Scenario, HeaderLen, PayloadLens>),
            ...);
    }

    /**
     * @brief Регистрирует сценарий для всех комбинаций размеров заголовка
     *        и тела пакета из фиксированного набора.
     *
     * @details
     * Вызывает @c RegisterScenario для заданного размера заголовка и
     * комбинируемых с ним размеров тела пакета. Вызовов @c RegisterScenario
     * может быть несколько. Имена бенчмарков имеют вид:
     * @code BM_PacketScenario/<name>/h<HeaderLen>/p<P>@endcode.
     *
     * @tparam Scenario Тип сценария из @c memcopy_bench::scenarios.
     *
     * @param name Короткое имя сценария, попадающее в имя бенчмарка
     *             (например, @c "Memcpy", @c "ComposedPrefetch").
     */
    template<class Scenario>
    void RegisterScenarioBothH(const char *name) {
        RegisterScenario<Scenario, 8, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
            16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608, 16777216, 33554432>(name);
        RegisterScenario<Scenario, 16, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
            16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608, 16777216, 33554432>(name);
        RegisterScenario<Scenario, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
            16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608, 16777216, 33554432>(name);
        RegisterScenario<Scenario, 64, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
            16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608, 16777216, 33554432>(name);
        RegisterScenario<Scenario, 128, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
            16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608, 16777216, 33554432>(name);
        RegisterScenario<Scenario, 256, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
            16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608, 16777216, 33554432>(name);
        RegisterScenario<Scenario, 512, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
            16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608, 16777216, 33554432>(name);
        RegisterScenario<Scenario, 1024, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
            16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608, 16777216, 33554432>(name);
    }

    /// @name Базовые сценарии
    /// Обёртки существующих методов копирования из @c lib/memory/copy/copy.h.
    ///@{

    /// @brief Побайтовое копирование в цикле. Минимальный baseline, без
    ///        векторизации и предвыборки.
    using Naive = sc::SimpleScenario<memory_bench::copy_naive>;

    /// @brief Обёртка над @c std::memcpy — основной baseline для сравнения
    ///        с предлагаемыми реализациями.
    using Baseline = sc::SimpleScenario<memory_bench::copy_memcpy>;

    /// @brief Копирование малых диапазонов (≤16 байт) перекрывающимися
    ///        записями.
    using Small = sc::SimpleScenario<memory_bench::copy_small_overlapping>;

    /// @brief Копирование 64-битными словами с побайтовым хвостом.
    using Words64 = sc::SimpleScenario<memory_bench::copy_words64>;
    ///@}

#if defined(__x86_64__) || defined(_M_X64)
    /// @name x86-специфичные сценарии
    /// Доступны только на x86_64. Реально регистрируются только при поддержке
    /// соответствующего CPU: SSE2-сценарии — при @c __builtin_cpu_supports("sse2"),
    /// AVX2-сценарии — при @c __builtin_cpu_supports("avx2"). Проверка в
    /// @c RegisteredScenarios.
    ///@{

    /// @brief SSE2 128-битное копирование, 4-кратное развёртывание.
    using SSE2 = sc::SimpleScenario<memory_bench::copy_sse2>;

    /// @brief AVX2 + предвыборка для диапазонов ≥ 1024 байт; иначе обычный AVX2.
    using Prefetch = sc::AVX2PrefetchAbove<1024>;

    /// @brief AVX2 с невременными (streaming) записями, обходящими кэш.
    using Stream = sc::SimpleScenario<memory_bench::copy_avx2_stream>;

    /// @brief libc на краях, SSE2 на выровненном теле.
    using MemcpyEdgesSSEBody = sc::AlignTarget<Baseline, SSE2, Baseline, 16>;

    /// @brief libc на краях, AVX2/prefetch на выровненном теле.
    using MemcpyEdgesAVXBody = sc::AlignTarget<Baseline, Prefetch, Baseline, 32>;

    /// @brief SSE2 на краях, libc на теле.
    using SSEEdgesMemcpyBody = sc::AlignTarget<SSE2, Baseline, SSE2, 16>;

    /// @brief AVX2/prefetch на краях, libc на теле.
    using AVXEdgesMemcpyBody = sc::AlignTarget<Prefetch, Baseline, Prefetch, 32>;

    /// @brief libc для коротких пакетов (< 256 байт), выровненный
    ///        AVX2-prefetch для длинных.
    using ComposedPrefetch = sc::ShortFallback<
        Baseline, 256, sc::AlignTarget<Baseline, Prefetch, Baseline, 32> >;

    /// @brief libc для коротких пакетов (< 256 байт), выровненный
    ///        AVX2-streaming для длинных.
    using ComposedStream = sc::ShortFallback<
        Baseline, 256, sc::AlignTarget<Baseline, Stream, Baseline, 32> >;

    /// @brief libc для коротких пакетов (< 64 байт), выровненный SSE2 для длинных.
    using ComposedSSE =
    sc::ShortFallback<Baseline, 64,
        sc::AlignTarget<Baseline, SSE2, Baseline, 16> >;
    ///@}
#endif
} // namespace

/**
 * @brief Регистрирует сценарии в Google Benchmark при старте процесса.
 *
 * @details
 * @c RegisteredScenarios вызывается один раз при статической инициализации. Все
 * регистрации через @c RegisterScenarioBothH добавляют бенчмарки с именами вида
 * @code BM_PacketScenario/<name>/h<HeaderLen>/p<P>@endcode.
 *
 * @warning На не-x86 платформах (например, Apple Silicon) x86-блок
 *          исключён препроцессором, и регистрируются только базовые
 *          сценарии.
 */
static const bool RegisteredScenarios = [] {
    RegisterScenarioBothH<Naive>("Naive");
    RegisterScenarioBothH<Baseline>("Memcpy");
    RegisterScenarioBothH<Small>("Small");
    RegisterScenarioBothH<Words64>("Words64");

#if ((defined(__x86_64__) || defined(_M_X64)) && (defined(__GNUC__) || defined(__clang__)))
    if (__builtin_cpu_supports("avx2")) {
        RegisterScenarioBothH<sc::VioletCopy>("VioletCopy");
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
