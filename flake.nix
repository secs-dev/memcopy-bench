{
  description = "Optional reproducible development environment for memcopy-bench";

  inputs.nixpkgs.url = "https://channels.nixos.org/nixpkgs-unstable/nixexprs.tar.zst";

  outputs = { nixpkgs, ... }:
    let
      supportedSystems = [
        "x86_64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs supportedSystems (system: f (import nixpkgs { inherit system; }));
    in
    {
      devShells = forAllSystems (pkgs:
        let
          # Bazel's local C++ toolchain is configured by a repository rule. Pass
          # the compiler through --repo_env so configuration is tracked and does
          # not rely solely on CC/CXX inherited from the development shell.
          bazel = pkgs.writeShellApplication {
            name = "bazel";
            runtimeInputs = [ pkgs.bazelisk ];
            text = ''
              args=()
              compiler_env_added=false

              for arg in "$@"; do
                args+=("$arg")
                if ! "$compiler_env_added"; then
                  case "$arg" in
                    aquery|build|coverage|cquery|fetch|query|run|sync|test)
                      args+=(--repo_env=CC=clang --repo_env=CXX=clang++)
                      compiler_env_added=true
                      ;;
                  esac
                fi
              done

              exec bazelisk "''${args[@]}"
            '';
          };
        in
        {
          default = pkgs.mkShell {
            packages = [
              bazel
              pkgs.buildifier
              pkgs.clang-tools
              pkgs.llvmPackages.clang
              pkgs.git
              pkgs.gnumake
            ];

            # These also make editor tools and commands run outside Bazel select
            # the pinned Clang. The bazel wrapper above handles Bazel itself.
            CC = "clang";
            CXX = "clang++";
          };
        });

      formatter = forAllSystems (pkgs: pkgs.nixfmt-rfc-style);
    };
}
