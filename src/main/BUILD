load("@rules_cc//cc:cc_binary.bzl", "cc_binary")

cc_binary(
    name = "main",
    srcs = glob(["*.cc"]),
    deps = [
        "//lib:memcpy_bench",
    ],
)
