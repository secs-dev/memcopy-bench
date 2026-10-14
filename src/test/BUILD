load("@rules_cc//cc:cc_test.bzl", "cc_test")


cc_test(
    name = "memcpy_bench_test",
    srcs = glob(["*.cc"]),
    deps = [
        "@googletest//:gtest_main",
        "//lib:memcpy_bench",
    ],
)
