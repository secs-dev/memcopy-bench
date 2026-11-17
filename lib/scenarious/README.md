# Draft scenario combinators

`scenarios.h` represents payload-copy scenarios as types with a static
`copy(void* dst, const void* src, std::size_t length)` function. Scenarios can be
template parameters of benchmarks without constructing objects.

```cpp
#include "lib/scenarious/scenarios.h"

namespace sc = memcopy_bench::scenarios;

using Small = sc::SimpleScenario<memory_bench::copy_small_overlapping>;
using Baseline = sc::SimpleScenario<memory_bench::copy_memcpy>;

// x86-64 only; requires an AVX2-capable CPU.
using PayloadOnly = sc::ShortFallback<
    Small, 64,
    sc::AlignTarget<Small, sc::AVX2PrefetchAbove<1024>, Small, 32>>;

// A benchmark templated on Scenario invokes:
// Scenario::copy(destination_payload, source_payload, payload_length);
```

The example thresholds are illustrative, not measured recommendations.

- `SimpleScenario<Method>` forwards a nonempty range to the copy method.
- `AlignSource<Head, Body, Tail, Alignment>` splits at source alignment.
- `AlignTarget<Head, Body, Tail, Alignment>` splits at destination alignment.
  Both use a minimal prefix, a body whose length is divisible by `Alignment`,
  and the remaining tail. Empty regions do not invoke child scenarios.
  Alignment must be positive; it need not be a power of two.
- `ShortFallback<ShortScenario, Threshold, LongScenario>` selects the short
  scenario for lengths strictly below the threshold, otherwise the long one.
- `AVX2PrefetchAbove<MinLength>` selects existing AVX2-prefetch for lengths
  greater than or equal to `MinLength`, otherwise existing AVX2. It is only
  available on x86-64 and does not detect CPU support at runtime.

Thresholds apply to the range received by that node. A prefetch selector inside
an alignment body sees the body length, not the original payload length.
Composition order therefore matters.

Alignment describes the address at the body's entry, not its instructions:
existing AVX2 methods still use unaligned loads/stores. Aligning one pointer
does not generally align the other. Nesting another alignment combinator can
also change the first alignment before reaching the innermost copy method.

Scenarios copy only the supplied payload range. Header offsets, allocations,
benchmark registration, and measurement remain outside this layer. Source and
destination ranges must be valid and nonoverlapping for nonzero lengths.
Zero-length calls do nothing and may receive null pointers.

The `scenarios` library and `scenarios_test` GoogleTest target are defined in
`BUILD.bazel`. Tests cover copying across lengths and offsets, destination guard
bytes, source preservation, alignment splitting, and fallback thresholds.
No benchmark registrations have been added.
