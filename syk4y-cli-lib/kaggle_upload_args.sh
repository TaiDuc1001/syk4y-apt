# Argument parsing and context initialization for `syk4y kaggle upload`.

kaggle_upload_usage() {
  cat "$SCRIPT_DIR/templates/kaggle-upload-usage.txt"
}

kaggle_upload_parse_args() {
  REPO_ROOT_OVERRIDE=""
  UPLOAD_ROOT_OVERRIDE=""
  VERSION_MESSAGE_OVERRIDE=""
  DIR_MODE_OVERRIDE=""
  FORCE_UPLOAD_OVERRIDE=0
  BUILD_WHEEL_ONLY=0
  ARTIFACT_FILTERS=()
  ARTIFACT_FILTER_IDS=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -u|--upload-dir)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for $1" >&2
          exit 2
        fi
        UPLOAD_ROOT_OVERRIDE="$2"
        shift 2
        ;;
      --force)
        FORCE_UPLOAD_OVERRIDE=1
        shift
        ;;
      -m|--message)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for $1" >&2
          exit 2
        fi
        VERSION_MESSAGE_OVERRIDE="$2"
        shift 2
        ;;
      --dir-mode)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for $1" >&2
          exit 2
        fi
        DIR_MODE_OVERRIDE="$2"
        shift 2
        ;;
      --repo-root)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for $1" >&2
          exit 2
        fi
        REPO_ROOT_OVERRIDE="$2"
        shift 2
        ;;
      --build-wheel-only)
        BUILD_WHEEL_ONLY=1
        shift
        ;;
      -h|--help)
        kaggle_upload_usage
        exit 0
        ;;
      --)
        shift
        while [[ $# -gt 0 ]]; do
          ARTIFACT_FILTERS+=("$1")
          shift
        done
        ;;
      -*)
        echo "Unknown option: $1" >&2
        kaggle_upload_usage >&2
        exit 2
        ;;
      *)
        ARTIFACT_FILTERS+=("$1")
        shift
        ;;
    esac
  done

  if [[ "${#ARTIFACT_FILTERS[@]}" -gt 0 ]]; then
    local artifact artifact_id
    local -A seen_filter_ids=()
    local -A filter_id_owner=()

    for artifact in "${ARTIFACT_FILTERS[@]}"; do
      if [[ -z "$artifact" ]]; then
        echo "Error: artifact name cannot be empty." >&2
        exit 2
      fi

      artifact_id="$(syk4y_slugify "$artifact")"
      if [[ -n "${filter_id_owner[$artifact_id]+x}" ]] && [[ "${filter_id_owner[$artifact_id]}" != "$artifact" ]]; then
        echo "Error: artifact slug collision between '${filter_id_owner[$artifact_id]}' and '$artifact' (slug: '$artifact_id')." >&2
        echo "Use distinct artifact names that slugify uniquely." >&2
        exit 2
      fi

      if [[ -z "${seen_filter_ids[$artifact_id]+x}" ]]; then
        seen_filter_ids["$artifact_id"]=1
        filter_id_owner["$artifact_id"]="$artifact"
        ARTIFACT_FILTER_IDS+=("$artifact_id")
      fi
    done
  fi
}

kaggle_upload_prepare_context() {
  if [[ -n "$REPO_ROOT_OVERRIDE" ]]; then
    if [[ "$REPO_ROOT_OVERRIDE" == /* ]]; then
      REPO_ROOT="$REPO_ROOT_OVERRIDE"
    else
      REPO_ROOT="$(cd "$REPO_ROOT_OVERRIDE" && pwd)"
    fi
  else
    REPO_ROOT="$(pwd)"
  fi

  if [[ ! -d "$REPO_ROOT" ]]; then
    echo "Error: invalid repository root: '$REPO_ROOT'." >&2
    exit 1
  fi

  REPO_NAME="$(basename "$REPO_ROOT")"
  BASE_DATASET_SLUG="$(syk4y_slugify "$REPO_NAME")"

  UPLOAD_ROOT="${KAGGLE_UPLOAD_ROOT:-$REPO_ROOT/kaggle_upload}"
  STATE_FILE="$UPLOAD_ROOT/.upload-state.json"

  ARTIFACT_IDS=()
  ALL_ARTIFACT_IDS=()

  DIR_MODE="${KAGGLE_DIR_MODE:-zip}"
  VERSION_MESSAGE="${KAGGLE_VERSION_MESSAGE:-Artifacts update $(date '+%Y-%m-%d %H:%M:%S')}"
  WHEELHOUSE_DATASET_DIR="$UPLOAD_ROOT/${BASE_DATASET_SLUG}-wheelhouse"
  WHEELHOUSE_PATH="$WHEELHOUSE_DATASET_DIR/wheelhouse.zip"
  WHEEL_JOBS="${WHEEL_JOBS:-16}"
  WHEELHOUSE_ZIP_MODE="${WHEELHOUSE_ZIP_MODE:-store}"
  WHEEL_FAIL_ON_MISSING="${WHEEL_FAIL_ON_MISSING:-0}"
  WHEELHOUSE_INPUT_KEY="__wheelhouse_input__"
  WHEELHOUSE_INPUT_HASH=""
  FORCE_UPLOAD="${KAGGLE_FORCE_UPLOAD:-0}"

  if [[ -n "$UPLOAD_ROOT_OVERRIDE" ]]; then
    UPLOAD_ROOT="$UPLOAD_ROOT_OVERRIDE"
    STATE_FILE="$UPLOAD_ROOT/.upload-state.json"
  fi
  if [[ "$FORCE_UPLOAD_OVERRIDE" -eq 1 ]]; then
    FORCE_UPLOAD=1
  fi
  if [[ -n "$VERSION_MESSAGE_OVERRIDE" ]]; then
    VERSION_MESSAGE="$VERSION_MESSAGE_OVERRIDE"
  fi
  if [[ -n "$DIR_MODE_OVERRIDE" ]]; then
    DIR_MODE="$DIR_MODE_OVERRIDE"
  fi

  # Recompute derived paths after applying CLI overrides.
  WHEELHOUSE_DATASET_DIR="$UPLOAD_ROOT/${BASE_DATASET_SLUG}-wheelhouse"
  WHEELHOUSE_PATH="$WHEELHOUSE_DATASET_DIR/wheelhouse.zip"

  declare -gA CURRENT_FP
  declare -gA CURRENT_META_FP
  declare -gA ARTIFACT_SOURCE_SPEC
  declare -gA ARTIFACT_ITEM_NAMES
  KAGGLE_CMD=()
}
