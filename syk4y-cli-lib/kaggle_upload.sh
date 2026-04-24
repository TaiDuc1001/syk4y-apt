# Shared implementation for `syk4y kaggle upload`.
# This file is intentionally thin and orchestrates purpose-specific modules.

# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/kaggle_upload_args.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/kaggle_upload_env.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/kaggle_upload_state.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/kaggle_upload_artifacts.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/kaggle_upload_wheelhouse.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/syk4y-cli-lib/kaggle_upload_transfer.sh"

kaggle_upload() (
  kaggle_upload_parse_args "$@"
  kaggle_upload_prepare_context
  kaggle_upload_run_flow
)
