# To learn more about how to use Nix to configure your environment
# see: https://developers.google.com/idx/guides/customize-idx-env
{ pkgs, ... }: {
  # Which nixpkgs channel to use.
  channel = "stable-24.05"; # or "unstable"
  # Use https://search.nixos.org/packages to find packages
  packages = [
    pkgs.python3
    pkgs.python3Packages.pip
    pkgs.uvicorn
  ];
  # Sets environment variables in the workspace
  env = {
    # This is the public URL of your workspace, used for auth redirects.
    APP_BASE_URL = "https://$IDX_WORKSPACE_URL";

    # ---------------- DANGER ----------------
    # It is not recommended to store secrets in this file.
    # Instead, use the IDX secrets manager.
    # For now, replace these with your actual Auth0 credentials.
    AUTH0_DOMAIN = "your-auth0-domain.auth0.com";
    AUTH0_CLIENT_ID = "your-auth0-client-id";
    AUTH0_CLIENT_SECRET = "your-auth0-client-secret";
    SESSION_SECRET = "a-very-secret-key-that-you-should-change";
    # ----------------------------------------
  };
  idx = {
    # Search for the extensions you want on https://open-vsx.org/ and use "publisher.id"
    extensions = [
      "ms-python.python"
      "google.gemini-cli-vscode-ide-companion"
    ];
    # Enable previews
    previews = {
      enable = true;
      previews = {
        web = {
          command = ["uvicorn" "api.main:app" "--host" "0.0.0.0" "--port" "$PORT"];
          manager = "web";
        };
      };
    };
    # Workspace lifecycle hooks
    workspace = {
      # Runs when a workspace is first created
      onCreate = {
        install-deps = "pip install -r requirements.txt";
        # Open editors for the following files by default, if they exist:
        default.openFiles = [ ".idx/dev.nix" "README.md" "api/main.py" ];
      };
      # Runs when the workspace is (re)started
      onStart = {
        # The web preview will start automatically
      };
    };
  };
}
