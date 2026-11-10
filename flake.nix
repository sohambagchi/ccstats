{
  description = "ccstats: archive Claude Code transcripts from many machines and serve usage stats";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      version = "0.0.11";
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        ccstats = pkgs.python3Packages.buildPythonApplication {
          pname = "ccstats";
          inherit version;
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python3Packages.setuptools ];
          # zoneinfo needs a tz database; rsync/ssh/iproute2 are called by collect and serve.
          dependencies = [ pkgs.python3Packages.tzdata ];
          # Suffix: the system ssh/rsync win (they honour its ssh config and patches); these are fallbacks.
          makeWrapperArgs = [ "--suffix" "PATH" ":" (pkgs.lib.makeBinPath ([ pkgs.rsync pkgs.openssh ] ++ pkgs.lib.optional pkgs.stdenv.isLinux pkgs.iproute2)) ];
          nativeCheckInputs = [ pkgs.python3Packages.pytestCheckHook ];
        };
        default = ccstats;
      });

      apps = forAll (pkgs: {
        default = { type = "app"; program = "${self.packages.${pkgs.system}.ccstats}/bin/ccstats"; };
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ (pkgs.python3.withPackages (ps: [ ps.pytest ps.tzdata ])) pkgs.sqlite pkgs.shellcheck ];  # rsync and ssh come from the system, as in the package
          shellHook = ''export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"'';
        };
      });
    };
}
