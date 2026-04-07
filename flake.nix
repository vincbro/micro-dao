
{
  description = "Basic Devshell";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs, ... }:
    let
      # This helper allows the shell to work on any system (Intel/ARM Linux/Mac)
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forEachSystem = f: nixpkgs.lib.genAttrs supportedSystems (system: f {
        pkgs = import nixpkgs { inherit system; };
      });
    in
    {
      devShells = forEachSystem ({ pkgs }: {
        default = pkgs.mkShell {
          nativeBuildInputs = [
            pkgs.python314
            pkgs.uv
            pkgs.pkg-config
            pkgs.ruff
            pkgs.basedpyright
          ];

          buildInputs = [
          ];

          shellHook = ''
          '';
        };
      });
    };
}
