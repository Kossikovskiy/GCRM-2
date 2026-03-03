{ pkgs, ... }:

let
  # Define the Python environment with all required packages
  pythonEnv = pkgs.python3.withPackages (ps: with ps; [
    (ps.mcp.overrideAttrs (old: {
      version = "1.0.0";
      src = ps.fetchPypi {
        pname = "mcp";
        inherit (old) version;
        sha256 = "07f917b960a7e0f2d807a1114b3fcf3560b2d6d0c7542f7c3272d5f6e85d8985";
      };
    }))
    sqlalchemy
    fastapi
    (uvicorn.override {-
      dependencies = [ pkgs.python3Packages.standard ];
    })
    pydantic
    httpx
    starlette
    (python-jose.override {
      extraBuildInputs = [ pkgs.python3Packages.cryptography ];
    })
    apscheduler
    python-dotenv
  ]);
in
{
  # Which nixpkgs channel to use.
  channel = "stable-24.05";

  # Use the Python environment we defined above.
  # This makes `python`, `pip`, and all packages available in the shell.
  packages = [ pythonEnv ];

  # Sets environment variables in the workspace
  env = {
    # This is the public URL of your workspace, used for auth redirects.
    APP_BASE_URL = "https://$IDX_WORKSPACE_URL";

    # ---------------- DANGER ----------------
    # Storing secrets in this file is not recommended for production
    # or shared projects. Please replace the placeholder values below
    # with your actual credentials.
AUTH0_DOMAIN=dev-80umollds5sbkqku.us.auth0.com
AUTH0_CLIENT_ID=tWfznxnflmcDEitZfkzlesHJ9YjZAZkN
AUTH0_CLIENT_SECRET=1FRpZiQxnpp8hF-xk7ihUCTof54kYXSw0x3RWzLbVD-sFrvSWQME-r13AYFVxCYL
SESSION_SECRET=56012663825725266809458956460929
    # ----------------------------------------
  };

  idx = {
    # Search for the extensions you want on https://open-vsx.org/
    extensions = [
      "ms-python.python"
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
      # No need to run "pip install" anymore, Nix handles it.
      onCreate = {
        default.openFiles = [ ".idx/dev.nix" "README.md" "api/main.py" ];
      };
      onStart = {
        # The web preview will start automatically
      };
    };
  };
}
