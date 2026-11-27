#pragma once

#include "lib/memory/copy/copy.h"
#include "lib/scenarious/scenarios.h"

namespace memcopy_bench::scenarios {

using CandidateScenario = ShortFallback<
    SimpleScenario<memory_bench::copy_memcpy>, 128,
    ShortFallback<
        SimpleScenario<memory_bench::copy_avx2_unrolled_small>, 513,
        ShortFallback<
            SimpleScenario<memory_bench::copy_memcpy>, 1024,
            ShortFallback<
                SimpleScenario<memory_bench::copy_avx2_prefetch>, 2049,
                ShortFallback<
                    SimpleScenario<memory_bench::copy_memcpy>, 32768,
                    ShortFallback<
                        SimpleScenario<
                            memory_bench::copy_avx2_aligned_temporal_t0>,
                        65537,
                        ShortFallback<
                            SimpleScenario<memory_bench::copy_memcpy>, 262144,
                            ShortFallback<
                                SimpleScenario<memory_bench::copy_rep_movsb>,
                                262145,
                                ShortFallback<
                                    SimpleScenario<memory_bench::copy_memcpy>,
                                    2097152,
                                    ShortFallback<
                                        SimpleScenario<
                                            memory_bench::
                                                copy_avx2_aligned_temporal_t0>,
                                        4194305,
                                        SimpleScenario<
                                            memory_bench::copy_memcpy>>>>>>>>>>>;

} // namespace memcopy_bench::scenarios
