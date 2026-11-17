# Draft scenario combinators

`scenarios.h` represents payload-copy scenarios as types with a static
`copy(void* dst, const void* src, std::size_t length)` function. Scenarios can be
template parameters of benchmarks without constructing objects.

```cpp
#include "lib/scenarious/scenarios.h"

namespace sc = memcopy_bench::scenarios;

using Small = sc::SimpleScenario<memory_bench::copy_small_overlapping>;
using Baseline = sc::SimpleScenario<memory_bench::copy_memcpy>;

using PayloadOnly = sc::ShortFallback<
    Small, 64,
    sc::AlignTarget<Small, sc::AVX2PrefetchAbove<1024>, Small, 32>>;

// A benchmark templated on Scenario invokes:
// PayloadOnly::copy(dst, src, len);
```

- `SimpleScenario<Method>` forwards a nonempty range to the copy method.
- `AlignSource<Head, Body, Tail, Alignment>` splits at source alignment.
- `AlignTarget<Head, Body, Tail, Alignment>` splits at destination alignment.
  Both use a minimal prefix, a body whose length is divisible by `Alignment`,
  and the remaining tail.
- `ShortFallback<ShortScenario, Threshold, LongScenario>` selects the short
  scenario for lengths strictly below the threshold, otherwise the long one.
- `AVX2PrefetchAbove<MinLength>` selects existing AVX2-prefetch for lengths
  greater than or equal to `MinLength`, otherwise existing AVX2.

Thresholds apply to the range received by that node. A prefetch selector inside
an alignment body sees the body length, not the original payload length.
Composition order therefore matters.

Alignment describes the address at the body's entry, not its instructions:
existing AVX2 methods still use unaligned loads/stores. Aligning one pointer
does not generally align the other. Nesting another alignment combinator can
also change the first alignment before reaching the innermost copy method.

