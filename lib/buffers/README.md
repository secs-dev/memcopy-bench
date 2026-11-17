# Buffers

The customers asked for benchmarks that better represent a real telecom network
stack. Our idea was to measure packet header assembly alongside memory
copying. For example, benchmark can extract the header before copying
the payload.

## `AbstractPacket`

`AbstractPacket<N, M>` is a mock packet for tests and benchmarks. It models an
`N`-byte header followed by an `M`-byte payload. Both sizes are positive
compile-time constants.

- `AbstractHeader` stores the `N` raw header bytes in `data`.
- `stripHeader(rawPtr, rawSize, header)` copies the first `N` bytes into
  `header.data` and returns a pointer to the payload in the input buffer.
  It returns `nullptr` if `rawPtr` is null or `rawSize` is less than `N + M`.

This mock can later be replaced with a GTP-U or PDCP implementation.
