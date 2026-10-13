load("@rules_cc//cc:cc_library.bzl", "cc_library")

cc_library(
    name = "memcpy_bench",
    srcs = ["memcpy_bench.cc"],
    hdrs = ["memcpy_bench.h"],
    deps = [
        "@google_benchmark//:benchmark",
    ],
    visibility = [
        "//main:__pkg__",
        "//test:__pkg__",
    ],
)
